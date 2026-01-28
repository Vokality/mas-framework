from __future__ import annotations

import asyncio

import grpc
import pytest

from mas import Agent
from mas.server import AgentDefinition

pytestmark = pytest.mark.asyncio


async def _wait_until(predicate, *, timeout: float = 2.0) -> None:
    start = asyncio.get_running_loop().time()
    while True:
        if predicate():
            return
        if asyncio.get_running_loop().time() - start > timeout:
            raise AssertionError("timeout")
        await asyncio.sleep(0.02)


async def _wait_until_async(predicate, *, timeout: float = 2.0) -> None:
    start = asyncio.get_running_loop().time()
    while True:
        if await predicate():
            return
        if asyncio.get_running_loop().time() - start > timeout:
            raise AssertionError("timeout")
        await asyncio.sleep(0.02)


async def test_agent_status_transitions(redis, mas_server_factory, test_tls) -> None:
    server = await mas_server_factory(
        {"worker": AgentDefinition(agent_id="worker", capabilities=[], metadata={})}
    )

    agent = Agent(
        "worker",
        server_addr=server.bound_addr,
        tls=test_tls.client("worker"),
    )

    await agent.start()
    try:

        async def status_is_active() -> bool:
            return await redis.hget("agent:worker", "status") == "ACTIVE"

        await _wait_until_async(status_is_active, timeout=2.0)
    finally:
        await agent.stop()

    async def status_is_inactive() -> bool:
        return await redis.hget("agent:worker", "status") == "INACTIVE"

    await _wait_until_async(status_is_inactive, timeout=2.0)


async def test_dlq_written_on_handler_error(
    redis, mas_server_factory, test_tls
) -> None:
    server = await mas_server_factory(
        {
            "sender": AgentDefinition(agent_id="sender", capabilities=[], metadata={}),
            "worker": AgentDefinition(agent_id="worker", capabilities=[], metadata={}),
        }
    )
    await server.authz.set_permissions("sender", allowed_targets=["worker"])

    class ErrorAgent(Agent):
        async def on_message(self, message) -> None:
            raise RuntimeError("boom")

    worker = ErrorAgent(
        "worker",
        server_addr=server.bound_addr,
        tls=test_tls.client("worker"),
    )
    sender = Agent(
        "sender",
        server_addr=server.bound_addr,
        tls=test_tls.client("sender"),
    )

    await worker.start()
    await sender.start()
    try:
        await sender.send("worker", "boom", {"value": 1})

        async def dlq_has_entry() -> bool:
            return await redis.xlen("dlq:messages") > 0

        await _wait_until_async(dlq_has_entry, timeout=2.0)
        entries = await redis.xrange("dlq:messages", count=1)
        assert entries
        _entry_id, fields = entries[0]
        assert fields.get("decision") == "DLQ"
        assert fields.get("reason", "").startswith("handler_error")
    finally:
        await sender.stop()
        await worker.stop()


async def test_correlation_id_expiry_on_late_reply(
    mas_server_factory, test_tls
) -> None:
    server = await mas_server_factory(
        {
            "requester": AgentDefinition(
                agent_id="requester", capabilities=[], metadata={}
            ),
            "responder": AgentDefinition(
                agent_id="responder", capabilities=[], metadata={}
            ),
        }
    )
    await server.authz.set_permissions("requester", allowed_targets=["responder"])
    await server.authz.set_permissions("responder", allowed_targets=["requester"])

    class SlowResponder(Agent):
        def __init__(self, agent_id: str, **kwargs) -> None:
            super().__init__(agent_id, **kwargs)
            self.reply_error: Exception | None = None
            self.reply_done = asyncio.Event()

        async def on_message(self, message) -> None:
            await asyncio.sleep(1.2)
            try:
                await message.reply("reply", {"ok": True})
            except Exception as exc:
                self.reply_error = exc
            finally:
                self.reply_done.set()

    responder = SlowResponder(
        "responder",
        server_addr=server.bound_addr,
        tls=test_tls.client("responder"),
    )
    requester = Agent(
        "requester",
        server_addr=server.bound_addr,
        tls=test_tls.client("requester"),
    )

    await responder.start()
    await requester.start()
    try:
        with pytest.raises(asyncio.TimeoutError):
            await requester.request("responder", "req", {"x": 1}, timeout=0.1)

        await responder.reply_done.wait()
        assert isinstance(responder.reply_error, grpc.aio.AioRpcError)
        assert responder.reply_error.code() in {
            grpc.StatusCode.INVALID_ARGUMENT,
            grpc.StatusCode.FAILED_PRECONDITION,
        }
        details = responder.reply_error.details() or ""
        assert (
            "correlation_id_expired" in details or "unknown_correlation_id" in details
        )
    finally:
        await requester.stop()
        await responder.stop()

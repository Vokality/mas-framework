from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from mas import Agent
from mas.server import AgentDefinition

pytestmark = pytest.mark.asyncio


class Ping(BaseModel):
    value: int


class TypedResponder(Agent[dict[str, object]]):
    @Agent.on("ping", model=Ping)
    async def handle_ping(self, message, payload: Ping) -> None:
        await message.reply("pong", {"value": payload.value + 1})

    @Agent.on("no_model")
    async def handle_no_model(self, message, payload: None) -> None:
        await message.reply("no_model.reply", {"ok": True})


async def _wait_for(predicate, *, timeout: float = 2.0) -> None:
    start = asyncio.get_running_loop().time()
    while True:
        if predicate():
            return
        if asyncio.get_running_loop().time() - start > timeout:
            raise AssertionError("timeout")
        await asyncio.sleep(0.02)


async def test_decorator_based_dispatch(mas_server_factory, test_tls) -> None:
    server = await mas_server_factory(
        {
            "sender": AgentDefinition(agent_id="sender", capabilities=[], metadata={}),
            "responder": AgentDefinition(
                agent_id="responder", capabilities=[], metadata={}
            ),
        }
    )
    await server.authz.set_permissions("sender", allowed_targets=["responder"])
    await server.authz.set_permissions("responder", allowed_targets=["sender"])

    responder = TypedResponder(
        "responder",
        server_addr=server.bound_addr,
        tls=test_tls.client("responder"),
    )
    sender = Agent(
        "sender",
        server_addr=server.bound_addr,
        tls=test_tls.client("sender"),
    )

    await responder.start()
    await sender.start()
    try:
        reply = await sender.request("responder", "ping", {"value": 1}, timeout=2)
        assert reply.message_type == "pong"
        assert reply.data["value"] == 2
    finally:
        await sender.stop()
        await responder.stop()


async def test_pydantic_validation_blocks_handler(mas_server_factory, test_tls) -> None:
    server = await mas_server_factory(
        {
            "sender": AgentDefinition(agent_id="sender", capabilities=[], metadata={}),
            "responder": AgentDefinition(
                agent_id="responder", capabilities=[], metadata={}
            ),
        }
    )
    await server.authz.set_permissions("sender", allowed_targets=["responder"])
    await server.authz.set_permissions("responder", allowed_targets=["sender"])

    responder = TypedResponder(
        "responder",
        server_addr=server.bound_addr,
        tls=test_tls.client("responder"),
    )
    sender = Agent(
        "sender",
        server_addr=server.bound_addr,
        tls=test_tls.client("sender"),
    )

    await responder.start()
    await sender.start()
    try:
        with pytest.raises(asyncio.TimeoutError):
            await sender.request(
                "responder",
                "ping",
                {"value": "not_an_int"},
                timeout=0.5,
            )
    finally:
        await sender.stop()
        await responder.stop()


async def test_handler_without_model(mas_server_factory, test_tls) -> None:
    server = await mas_server_factory(
        {
            "sender": AgentDefinition(agent_id="sender", capabilities=[], metadata={}),
            "responder": AgentDefinition(
                agent_id="responder", capabilities=[], metadata={}
            ),
        }
    )
    await server.authz.set_permissions("sender", allowed_targets=["responder"])
    await server.authz.set_permissions("responder", allowed_targets=["sender"])

    responder = TypedResponder(
        "responder",
        server_addr=server.bound_addr,
        tls=test_tls.client("responder"),
    )
    sender = Agent(
        "sender",
        server_addr=server.bound_addr,
        tls=test_tls.client("sender"),
    )

    await responder.start()
    await sender.start()
    try:
        reply = await sender.request("responder", "no_model", {}, timeout=2)
        assert reply.message_type == "no_model.reply"
        assert reply.data["ok"] is True
    finally:
        await sender.stop()
        await responder.stop()


async def test_fallback_to_on_message(mas_server_factory, test_tls) -> None:
    server = await mas_server_factory(
        {
            "sender": AgentDefinition(agent_id="sender", capabilities=[], metadata={}),
            "worker": AgentDefinition(agent_id="worker", capabilities=[], metadata={}),
        }
    )
    await server.authz.set_permissions("sender", allowed_targets=["worker"])

    received: list[str] = []

    class Worker(Agent):
        async def on_message(self, message) -> None:
            received.append(message.message_type)

    worker = Worker(
        "worker", server_addr=server.bound_addr, tls=test_tls.client("worker")
    )
    sender = Agent(
        "sender", server_addr=server.bound_addr, tls=test_tls.client("sender")
    )

    await worker.start()
    await sender.start()
    try:
        await sender.send("worker", "unhandled", {"x": 1})
        await _wait_for(lambda: received == ["unhandled"])
    finally:
        await sender.stop()
        await worker.stop()

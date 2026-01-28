from __future__ import annotations

import asyncio

import pytest

from mas import Agent
from mas.server import AgentDefinition
from pydantic import BaseModel

pytestmark = pytest.mark.asyncio


async def _wait_until(predicate, *, timeout: float = 2.0) -> None:
    start = asyncio.get_running_loop().time()
    while True:
        if predicate():
            return
        if asyncio.get_running_loop().time() - start > timeout:
            raise AssertionError("timeout")
        await asyncio.sleep(0.02)


class TestMultiInstance:
    async def test_messages_distributed_across_instances(
        self, mas_server_factory, test_tls
    ) -> None:
        server = await mas_server_factory(
            {
                "sender": AgentDefinition(
                    agent_id="sender", capabilities=["send"], metadata={}
                ),
                "worker": AgentDefinition(
                    agent_id="worker", capabilities=["work"], metadata={}
                ),
            }
        )
        await server.authz.set_permissions("sender", allowed_targets=["worker"])

        class Worker(Agent):
            def __init__(self, agent_id: str, **kwargs):
                super().__init__(agent_id, **kwargs)
                self.processed = 0

            async def on_message(self, message):
                self.processed += 1

        worker1 = Worker(
            "worker", server_addr=server.bound_addr, tls=test_tls.client("worker")
        )
        worker2 = Worker(
            "worker", server_addr=server.bound_addr, tls=test_tls.client("worker")
        )
        sender = Agent(
            "sender", server_addr=server.bound_addr, tls=test_tls.client("sender")
        )

        await worker1.start()
        await worker2.start()
        await sender.start()

        try:
            for i in range(30):
                await sender.send("worker", "work.item", {"i": i})

            await _wait_until(lambda: worker1.processed + worker2.processed >= 30)
            assert worker1.processed > 0
            assert worker2.processed > 0
        finally:
            await sender.stop()
            await worker1.stop()
            await worker2.stop()

    async def test_reply_routes_to_origin_instance(
        self, mas_server_factory, test_tls
    ) -> None:
        server = await mas_server_factory(
            {
                "requester": AgentDefinition(
                    agent_id="requester", capabilities=["request"], metadata={}
                ),
                "responder": AgentDefinition(
                    agent_id="responder", capabilities=["respond"], metadata={}
                ),
            }
        )
        await server.authz.set_permissions("requester", allowed_targets=["responder"])
        await server.authz.set_permissions("responder", allowed_targets=["requester"])

        class Responder(Agent):
            async def on_message(self, message):
                await message.reply(
                    "reply.message",
                    {"saw_sender_instance": message.meta.sender_instance_id},
                )

        requester1 = Agent(
            "requester",
            server_addr=server.bound_addr,
            tls=test_tls.client("requester"),
        )
        requester2 = Agent(
            "requester",
            server_addr=server.bound_addr,
            tls=test_tls.client("requester"),
        )
        responder = Responder(
            "responder",
            server_addr=server.bound_addr,
            tls=test_tls.client("responder"),
        )

        await responder.start()
        await requester1.start()
        await requester2.start()

        try:
            r1 = await requester1.request(
                "responder", "req.message", {"x": 1}, timeout=2
            )
            r2 = await requester2.request(
                "responder", "req.message", {"x": 2}, timeout=2
            )

            assert r1.data["saw_sender_instance"] == requester1.instance_id
            assert r2.data["saw_sender_instance"] == requester2.instance_id
        finally:
            await requester1.stop()
            await requester2.stop()
            await responder.stop()

    async def test_state_shared_across_instances(
        self, mas_server_factory, test_tls
    ) -> None:
        server = await mas_server_factory(
            {
                "worker": AgentDefinition(
                    agent_id="worker", capabilities=["work"], metadata={}
                )
            }
        )

        class CounterState(BaseModel):
            count: int = 0

        agent1 = Agent(
            "worker",
            server_addr=server.bound_addr,
            tls=test_tls.client("worker"),
            state_model=CounterState,
        )
        agent2 = Agent(
            "worker",
            server_addr=server.bound_addr,
            tls=test_tls.client("worker"),
            state_model=CounterState,
        )

        await agent1.start()
        await agent2.start()

        try:
            await agent1.update_state({"count": 42})
            await agent2.refresh_state()
            assert agent2.state.count == 42
        finally:
            await agent1.stop()
            await agent2.stop()

    async def test_discovery_is_scoped_by_permissions(
        self, mas_server_factory, test_tls
    ) -> None:
        server = await mas_server_factory(
            {
                "discoverer": AgentDefinition(
                    agent_id="discoverer", capabilities=["discover"], metadata={}
                ),
                "worker": AgentDefinition(
                    agent_id="worker", capabilities=["special"], metadata={}
                ),
            }
        )
        await server.authz.set_permissions("discoverer", allowed_targets=["worker"])

        discoverer = Agent(
            "discoverer",
            server_addr=server.bound_addr,
            tls=test_tls.client("discoverer"),
        )
        worker = Agent(
            "worker", server_addr=server.bound_addr, tls=test_tls.client("worker")
        )

        await worker.start()
        await discoverer.start()

        try:
            results = await discoverer.discover(capabilities=["special"])
            assert len(results) == 1
            assert results[0]["id"] == "worker"
        finally:
            await discoverer.stop()
            await worker.stop()

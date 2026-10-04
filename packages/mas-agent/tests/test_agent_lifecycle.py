"""Regressions for resource cleanup and transport readiness across lifecycles."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import grpc
import grpc.aio as grpc_aio
import pytest
from mas_agent.agent import Agent
from mas_agent.config import TlsClientConfig
from mas_proto.runtime.v1 import runtime_pb2 as mas_pb2
from mas_proto.runtime.v1 import runtime_pb2_grpc as mas_pb2_grpc


class _ControlledAgent(Agent):
    """Allow startup to pause before the server welcomes its transport."""

    def __init__(self, tls: TlsClientConfig) -> None:
        super().__init__("worker", tls=tls)
        self.transport_started = asyncio.Event()
        self.allow_welcome = asyncio.Event()

    async def _transport_loop(self) -> None:
        self.transport_started.set()
        await self.allow_welcome.wait()
        self._transport_ready.set()
        await asyncio.Event().wait()

    async def _load_state(self) -> None:
        return


@pytest.fixture
def controlled_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> _ControlledAgent:
    credentials = tmp_path / "credentials.pem"
    credentials.write_bytes(b"test credentials")

    def secure_channel(
        target: str, credentials: grpc.ChannelCredentials
    ) -> grpc_aio.Channel:
        del credentials
        return grpc_aio.insecure_channel(target)

    monkeypatch.setattr(grpc_aio, "secure_channel", secure_channel)
    return _ControlledAgent(
        TlsClientConfig(str(credentials), str(credentials), str(credentials))
    )


@pytest.mark.asyncio
async def test_duplicate_start_is_rejected_without_replacing_channel(
    controlled_agent: _ControlledAgent,
) -> None:
    controlled_agent.allow_welcome.set()
    await controlled_agent.start()
    channel = controlled_agent._channel
    transport = controlled_agent._transport_task
    try:
        with pytest.raises(RuntimeError, match="already started"):
            await controlled_agent.start()
        assert controlled_agent._channel is channel
        assert controlled_agent._transport_task is transport
    finally:
        await controlled_agent.stop()


@pytest.mark.asyncio
async def test_cancelled_start_closes_channel_and_transport(
    controlled_agent: _ControlledAgent,
) -> None:
    task = asyncio.create_task(controlled_agent.start())
    await controlled_agent.transport_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert controlled_agent._channel is None
    assert controlled_agent._transport_task is None
    assert not controlled_agent._running


@pytest.mark.asyncio
async def test_failed_start_calls_stop_hook_before_resource_cleanup(
    controlled_agent: _ControlledAgent,
) -> None:
    stopped = asyncio.Event()

    class FailingStartupAgent(_ControlledAgent):
        async def on_start(self) -> None:
            raise ValueError("start hook failed")

        async def on_stop(self) -> None:
            stopped.set()

    assert controlled_agent.tls is not None
    agent = FailingStartupAgent(controlled_agent.tls)
    agent.allow_welcome.set()
    with pytest.raises(ValueError, match="start hook failed"):
        await agent.start()
    assert stopped.is_set()
    assert agent._channel is None


@pytest.mark.asyncio
async def test_restart_waits_for_a_new_welcome(
    controlled_agent: _ControlledAgent,
) -> None:
    controlled_agent.allow_welcome.set()
    await controlled_agent.start()
    await controlled_agent.stop()
    controlled_agent.transport_started.clear()
    controlled_agent.allow_welcome.clear()
    task = asyncio.create_task(controlled_agent.start())
    try:
        await controlled_agent.transport_started.wait()
        assert not task.done()
        assert not controlled_agent._transport_ready.is_set()
    finally:
        controlled_agent.allow_welcome.set()
        await task
        await controlled_agent.stop()


@pytest.mark.asyncio
async def test_stop_hook_failure_still_closes_resources() -> None:
    class FailingStopAgent(Agent):
        async def on_stop(self) -> None:
            raise ValueError("hook failed")

    agent = FailingStopAgent("worker")
    agent._running = True
    agent._channel = grpc_aio.insecure_channel("localhost:1")
    agent._stub = mas_pb2_grpc.RuntimeServiceStub(agent._channel)
    with pytest.raises(ValueError, match="hook failed"):
        await agent.stop()
    assert agent._channel is None
    assert agent._stub is None
    assert not agent._transport_ready.is_set()


@pytest.mark.asyncio
async def test_cancelled_stop_still_closes_transport_and_channel() -> None:
    draining = asyncio.Event()

    class DrainingAgent(Agent):
        async def _drain_handler_tasks(self, timeout: float = 5) -> None:
            del timeout
            draining.set()
            await asyncio.Event().wait()

    agent = DrainingAgent("worker")
    agent._channel = grpc_aio.insecure_channel("localhost:1")
    agent._stub = mas_pb2_grpc.RuntimeServiceStub(agent._channel)
    agent._transport_task = asyncio.create_task(asyncio.sleep(100))
    stop = asyncio.create_task(agent.stop())
    await draining.wait()
    stop.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stop
    assert agent._transport_task is None
    assert agent._channel is None


@pytest.mark.asyncio
async def test_transport_reconnects_with_hello_before_queued_ack() -> None:
    class ReconnectingStub(mas_pb2_grpc.RuntimeServiceStub):
        def __init__(self) -> None:
            self.connected = asyncio.Event()
            self.attempts = 0
            self.first_events: list[mas_pb2.ClientEvent] = []

        async def Transport(
            self,
            request_iterator: AsyncIterator[mas_pb2.ClientEvent],
            metadata: list[tuple[str, str]] | None = None,
        ) -> AsyncIterator[mas_pb2.ServerEvent]:
            del metadata
            self.attempts += 1
            self.first_events.append(await anext(request_iterator))
            yield mas_pb2.ServerEvent(welcome=mas_pb2.Welcome())
            if self.attempts == 1:
                raise ConnectionError("server disconnected")
            self.connected.set()
            await asyncio.Event().wait()

    agent = Agent("worker")
    stub = ReconnectingStub()
    agent._stub = stub
    agent._running = True
    await agent._outgoing.put(
        mas_pb2.ClientEvent(ack=mas_pb2.Ack(delivery_id="old-session"))
    )
    transport = asyncio.create_task(agent._transport_loop())
    try:
        await asyncio.wait_for(stub.connected.wait(), timeout=0.5)
        assert stub.attempts == 2
        assert all(event.HasField("hello") for event in stub.first_events)
        assert agent._transport_ready.is_set()
    finally:
        agent._running = False
        transport.cancel()
        await asyncio.gather(transport, return_exceptions=True)
    assert not agent._transport_ready.is_set()

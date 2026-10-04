"""Exercise agent recovery on a real mTLS stream with durable broker delivery."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import grpc
import grpc.aio as grpc_aio
import pytest
from mas_agent.agent import Agent
from mas_agent.config import TlsClientConfig
from mas_core.protocol import EnvelopeMessage
from mas_gateway.config import GatewaySettings, RedisSettings
from mas_proto.runtime.v1 import runtime_pb2 as mas_pb2
from mas_proto.runtime.v1 import runtime_pb2_grpc as mas_pb2_grpc
from mas_server.dev import dev_server_settings, generate_dev_tls
from mas_server.runtime import MASServer
from mas_server.types import AgentDefinition


class _RecordingStub(mas_pb2_grpc.RuntimeServiceStub):
    """Retain the concrete stream so a test can interrupt its connection."""

    def __init__(self, channel: grpc_aio.Channel) -> None:
        self._runtime = mas_pb2_grpc.RuntimeServiceStub(channel)
        self.call: (
            grpc_aio.StreamStreamCall[mas_pb2.ClientEvent, mas_pb2.ServerEvent] | None
        ) = None
        self.connections = 0

    def Transport(
        self,
        request_iterator: AsyncIterator[mas_pb2.ClientEvent],
        metadata: list[tuple[str, str]] | None = None,
    ) -> grpc_aio.StreamStreamCall[mas_pb2.ClientEvent, mas_pb2.ServerEvent]:
        self.connections += 1
        self.call = self._runtime.Transport(request_iterator, metadata=metadata)
        return self.call


@pytest.mark.asyncio
async def test_delivery_continues_after_transport_stream_disconnect(
    tmp_path: Path,
) -> None:
    tls = generate_dev_tls(tmp_path / "tls", agent_ids=frozenset({"sender", "worker"}))
    server = MASServer(
        settings=dev_server_settings(
            agents={
                agent_id: AgentDefinition(
                    agent_id=agent_id, capabilities=[], metadata={}
                )
                for agent_id in ("sender", "worker")
            },
            tls=tls,
            listen_addr="127.0.0.1:0",
        ),
        gateway=GatewaySettings(redis=RedisSettings(url="redis://localhost:6379")),
    )
    await server.start()
    await server.authz.set_permissions("sender", allowed_targets=["worker"])
    received = asyncio.Event()

    class Worker(Agent):
        async def on_message(self, message: EnvelopeMessage) -> None:
            assert message.data == {"value": 1}
            received.set()

    worker = Worker("worker")
    client_tls = tls.client("worker")
    channel = grpc_aio.secure_channel(
        server.bound_addr,
        grpc.ssl_channel_credentials(
            root_certificates=Path(client_tls.root_ca_path).read_bytes(),
            private_key=Path(client_tls.client_key_path).read_bytes(),
            certificate_chain=Path(client_tls.client_cert_path).read_bytes(),
        ),
    )
    stub = _RecordingStub(channel)
    worker._channel = channel
    worker._stub = stub
    worker._running = True
    worker._transport_task = asyncio.create_task(worker._transport_loop())
    sender_tls = tls.client("sender")
    sender = Agent(
        "sender",
        server_addr=server.bound_addr,
        tls=TlsClientConfig(
            sender_tls.root_ca_path,
            sender_tls.client_cert_path,
            sender_tls.client_key_path,
        ),
    )
    try:
        await worker.wait_transport_ready(timeout=2)
        assert stub.call is not None
        stub.call.cancel()
        async with asyncio.timeout(2):
            while stub.connections < 2 or not worker._transport_ready.is_set():
                await asyncio.sleep(0.01)
        await sender.start()
        await sender.send("worker", "work", {"value": 1})
        await asyncio.wait_for(received.wait(), timeout=2)
    finally:
        await sender.stop()
        await worker.stop()
        await server.stop()

"""Run two local MAS brokers, a dashboard and agents exchanging real requests.

Requires Redis and openssl. Uses Redis database 15 by default without flushing it.
Pass --otlp-endpoint http://localhost:4318 to export fully sampled traces.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from grpc.aio import AioRpcError
from mas_agent.agent import Agent
from mas_agent.config import TlsClientConfig
from mas_agent.handlers import AgentMessage
from mas_core.observability import ObservationSettings
from mas_core.telemetry.runtime import get_telemetry
from mas_gateway.config import (
    FeaturesSettings,
    GatewaySettings,
    RateLimitSettings,
    RedisSettings,
    TelemetrySettings,
)
from mas_server.dev import dev_server_settings, generate_dev_tls
from mas_server.management import ManagementSettings
from mas_server.runtime import MASServer
from mas_server.types import AgentDefinition
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class Ping(BaseModel):
    """Small typed message used to exercise the full broker path."""

    sequence: int


class Worker(Agent[BaseModel]):
    """Respond to typed requests over the authenticated runtime transport."""

    @Agent.on("ping", model=Ping)
    async def ping(self, message: AgentMessage, payload: Ping) -> None:
        """Reply so request correlation and acknowledgements are exercised."""
        await self.send_reply_envelope(message, "pong", {"sequence": payload.sequence})


class Arguments(argparse.Namespace):
    """Typed command-line options for the example listener."""

    port: int = 8080
    otlp_endpoint: str | None = None


async def main(port: int, otlp_endpoint: str | None = None) -> None:
    """Start the example resources and keep the dashboard available."""
    definitions = {
        "room_sender": AgentDefinition("room_sender", ["requests"], {}),
        "room_worker": AgentDefinition("room_worker", ["ping"], {}),
    }
    gateway = GatewaySettings(
        redis=RedisSettings(
            url=os.environ.get("MAS_REDIS_URL", "redis://localhost:6379/15")
        ),
        rate_limit=RateLimitSettings(per_minute=60, per_hour=3600),
        features=FeaturesSettings(rbac=True),
        telemetry=TelemetrySettings(
            enabled=True,
            service_name="mas-control-room",
            sample_ratio=1.0,
            otlp_endpoint=otlp_endpoint,
            headers={},
        ),
    )
    with TemporaryDirectory(prefix="mas-control-room-") as directory:
        tls = await asyncio.to_thread(
            generate_dev_tls, Path(directory), agent_ids=frozenset(definitions)
        )
        settings = replace(
            dev_server_settings(agents=definitions, tls=tls, listen_addr="127.0.0.1:0"),
            management=ManagementSettings(port=port),
            broker_id="room-west",
            observations=ObservationSettings(trace_sample_every=1),
        )
        server = MASServer(settings=settings, gateway=gateway)
        peer = MASServer(
            settings=replace(settings, broker_id="room-east", management=None),
            gateway=gateway,
        )
        agents: list[Agent[BaseModel]] = []
        try:
            await server.start()
            await peer.start()
            for agent_id, target_id in (
                ("room_sender", "room_worker"),
                ("room_worker", "room_sender"),
            ):
                await server.authz.set_permissions(
                    agent_id, allowed_targets=[], blocked_targets=[]
                )
                role = f"{agent_id}_role"
                await server.authz.delete_role(role)
                await server.authz.create_role(role, permissions=[f"send:{target_id}"])
                await server.authz.assign_role(agent_id, role)
            for agent_id, cls in [("room_worker", Worker), ("room_sender", Agent)]:
                client = tls.client(agent_id)
                agent = cls(
                    agent_id,
                    server_addr=peer.bound_addr
                    if agent_id == "room_worker"
                    else server.bound_addr,
                    tls=TlsClientConfig(
                        root_ca_path=client.root_ca_path,
                        client_cert_path=client.client_cert_path,
                        client_key_path=client.client_key_path,
                    ),
                )
                agents.append(agent)
                await agent.start()
            sender = agents[1]
            print(f"MAS dashboard: {server.management_url}", flush=True)
            sequence = 0
            while True:
                try:
                    await sender.request(
                        "room_worker", "ping", {"sequence": sequence}, timeout=5
                    )
                except (AioRpcError, RuntimeError, TimeoutError):
                    logger.exception("Example request failed")
                sequence += 1
                await asyncio.sleep(3)
        finally:
            await asyncio.gather(
                *(agent.stop() for agent in agents), return_exceptions=True
            )
            try:
                await peer.stop()
                await server.stop()
            finally:
                await get_telemetry().shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--otlp-endpoint",
        help="Optional OTLP/HTTP collector base URL, for example http://localhost:4318",
    )
    args = Arguments()
    parser.parse_args(namespace=args)
    logging.basicConfig(level=logging.WARNING)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main(args.port, args.otlp_endpoint))

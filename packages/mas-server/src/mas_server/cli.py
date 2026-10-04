"""Run a configured MAS broker under a process supervisor."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
from pathlib import Path
from typing import Literal

from mas_core.telemetry.runtime import get_telemetry
from mas_gateway.config import GatewaySettings
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .runtime import MASServer
from .types import MASServerSettings


class Arguments(argparse.Namespace):
    """Typed broker configuration paths supplied by the operator."""

    settings: Path
    gateway: Path | None = None


class BrokerReady(BaseModel):
    """Supervisor readiness event with the broker's actual bound endpoints."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event: Literal["mas.broker.ready"] = "mas.broker.ready"
    pid: int = Field(gt=0)
    listen_addr: str = Field(min_length=1)
    management_url: str | None = None


async def serve(settings: MASServerSettings, gateway: GatewaySettings) -> None:
    """Start a broker and drain it when the supervisor requests shutdown."""
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopping.set)
    server = MASServer(settings=settings, gateway=gateway)
    try:
        await server.start()
        logging.getLogger(__name__).info(
            "Broker ready", extra={"listen_addr": server.bound_addr}
        )
        print(
            BrokerReady(
                pid=os.getpid(),
                listen_addr=server.bound_addr,
                management_url=server.management_url
                if settings.management is not None
                else None,
            ).model_dump_json(),
            flush=True,
        )
        await stopping.wait()
    finally:
        try:
            await server.stop()
        finally:
            for signum in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(signum)
            await get_telemetry().shutdown()


def main() -> None:
    """Validate configuration boundaries before constructing runtime services."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, required=True, help="Broker JSON file")
    parser.add_argument(
        "--gateway", type=Path, help="Gateway YAML file; env also applies"
    )
    arguments = Arguments()
    parser.parse_args(namespace=arguments)
    settings = TypeAdapter(MASServerSettings).validate_json(
        arguments.settings.read_bytes()
    )
    gateway = (
        GatewaySettings.from_yaml(str(arguments.gateway))
        if arguments.gateway is not None
        else GatewaySettings()
    )
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve(settings, gateway))

"""Serve the real packaged management UI; Playwright intercepts operational APIs."""

from __future__ import annotations

import asyncio
import signal

from mas_gateway.audit import AuditModule
from mas_gateway.config import GatewaySettings
from mas_server.management import ManagementService, ManagementSettings
from mas_server.sessions import SessionManager
from redis.asyncio import Redis


async def serve() -> None:
    """Use actual routing, assets and CSP with a lazy unreachable Redis backend."""
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopping.set)
    redis = Redis.from_url(
        "redis://127.0.0.1:1", decode_responses=True, socket_connect_timeout=0.05
    )
    audit = AuditModule(redis, file_sink=None)
    service = ManagementService(
        settings=ManagementSettings(port=4173),
        redis=redis,
        sessions=SessionManager(agents={}, redis=redis),
        agents={},
        gateway=GatewaySettings(),
        audit=audit,
        circuit_breaker=None,
        is_running=lambda: True,
    )
    try:
        await service.start()
        print(service.url, flush=True)
        await stopping.wait()
    finally:
        try:
            await service.stop()
            await audit.close()
        finally:
            await redis.aclose()
            for signum in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(signum)


if __name__ == "__main__":
    asyncio.run(serve())

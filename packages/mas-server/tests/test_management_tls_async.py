"""Management TLS bootstrap yields and cleans up cancelled startup."""

from __future__ import annotations

import asyncio
import ssl
import threading

import pytest
from mas_gateway.audit import AuditModule
from mas_gateway.config import GatewaySettings
from mas_server.management import (
    ManagementService,
    ManagementSettings,
    ManagementTlsSettings,
)
from mas_server.sessions import SessionManager
from redis.asyncio import Redis


def management(redis: Redis) -> ManagementService:
    return ManagementService(
        settings=ManagementSettings(
            port=0, tls=ManagementTlsSettings("certificate.pem", "key.pem")
        ),
        redis=redis,
        sessions=SessionManager(agents={}, redis=redis),
        agents={},
        gateway=GatewaySettings(),
        audit=AuditModule(redis, file_sink=None),
        circuit_breaker=None,
        is_running=lambda: True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_tls_bootstrap_yields_and_cancelled_runner_is_closed(
    monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    loop_thread = threading.get_ident()
    contexts: list[ssl.SSLContext] = []

    def load_cert_chain(context: ssl.SSLContext, certfile: str, keyfile: str) -> None:
        assert threading.get_ident() != loop_thread
        assert (certfile, keyfile) == ("certificate.pem", "key.pem")
        assert context.minimum_version == ssl.TLSVersion.TLSv1_2
        contexts.append(context)
        entered.set()
        try:
            assert release.wait(1), "TLS loader was not released"
        finally:
            finished.set()

    monkeypatch.setattr(ssl.SSLContext, "load_cert_chain", load_cert_chain)
    redis = Redis.from_url("redis://127.0.0.1:6379", decode_responses=True)
    service = management(redis)
    task = asyncio.create_task(service.start())
    try:
        assert await asyncio.to_thread(entered.wait, 0.5)
        await asyncio.sleep(0.01)
        assert not task.done()
        assert service._runner is not None
        with pytest.raises(RuntimeError, match="not started"):
            _ = service.url
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 0.2)
            assert service._runner is None
        release.set()
        assert await asyncio.to_thread(finished.wait, 0.5)
        if not cancel:
            await task
            assert service.url.startswith("https://127.0.0.1:")
            await service.stop()
        await service.start()
        assert len(contexts) == 2
        assert contexts[0] is not contexts[1]
        assert service._dashboard_assets["/"].content_type == "text/html"
        assert service._dashboard_assets["/"].body
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert await asyncio.to_thread(finished.wait, 0.5)
        await service.stop()
        await redis.aclose()


@pytest.mark.asyncio
async def test_tls_loader_failure_closes_runner() -> None:
    redis = Redis.from_url("redis://127.0.0.1:6379", decode_responses=True)
    service = management(redis)
    try:
        for _ in range(2):
            with pytest.raises(FileNotFoundError):
                await service.start()
            assert service._runner is None
            with pytest.raises(RuntimeError, match="not started"):
                _ = service.url
    finally:
        await service.stop()
        await redis.aclose()

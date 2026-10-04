"""Dashboard routing and startup-loaded assets need no backend connection."""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from aiohttp import ClientSession
from mas_gateway.audit import AuditModule
from mas_gateway.config import GatewaySettings
from mas_server import management as management_module
from mas_server.management import ManagementService, ManagementSettings
from mas_server.sessions import SessionManager
from redis.asyncio import Redis
from yarl import URL

_INDEX = b'<!doctype html><title>Control room</title><div id="app"></div>'
_ASSETS = {
    "index.html": _INDEX,
    "assets/dashboard.js": b'document.title = "Control room";',
    "assets/dashboard.css": b"body { color: #162427; }",
}


@pytest.fixture
async def dashboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[ManagementService]:
    bundle = tmp_path / "dashboard_assets"
    for name, body in _ASSETS.items():
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    loop_thread = threading.get_ident()

    def resources(package: str) -> Path:
        assert package == "mas_server"
        assert threading.get_ident() != loop_thread
        return tmp_path

    monkeypatch.setattr(management_module, "files", resources)
    redis = Redis.from_url("redis://127.0.0.1:1", decode_responses=True)
    service = ManagementService(
        settings=ManagementSettings(port=0, auth_mode="token", token="reader"),
        redis=redis,
        sessions=SessionManager(agents={}, redis=redis),
        agents={},
        gateway=GatewaySettings(),
        audit=AuditModule(redis, file_sink=None),
        circuit_breaker=None,
        is_running=lambda: True,
    )
    await service.start()

    def no_disk_reads(package: str) -> Path:
        raise AssertionError(f"Per-request resource lookup: {package}")

    monkeypatch.setattr(management_module, "files", no_disk_reads)
    try:
        yield service
    finally:
        await service.stop()
        await redis.aclose()


@pytest.mark.asyncio
async def test_dashboard_pages_and_assets_use_startup_cache(
    dashboard: ManagementService,
) -> None:
    pages = (
        "/",
        "/overview",
        "/fleet",
        "/performance",
        "/traces",
        "/traces/" + "a" * 32,
        "/alerts",
        "/agents",
        "/queues",
        "/activity",
        "/telemetry",
    )
    async with ClientSession() as client:
        for path in pages:
            async with client.get(dashboard.url + path) as response:
                assert response.status == 200
                assert response.content_type == "text/html"
                assert await response.read() == _INDEX
                assert response.headers["Cache-Control"] == "no-store"
                assert response.headers["X-Content-Type-Options"] == "nosniff"
                csp = response.headers["Content-Security-Policy"]
                assert "script-src 'self';" in csp
                assert "style-src 'self';" in csp
                assert "style-src-attr 'unsafe-inline';" in csp
                assert "frame-ancestors 'none';" in csp
        for path, content_type in (
            ("assets/dashboard.js", "text/javascript"),
            ("assets/dashboard.css", "text/css"),
        ):
            async with client.get(dashboard.url + "/" + path) as response:
                assert response.status == 200
                assert response.content_type == content_type
                assert await response.read() == _ASSETS[path]
                assert response.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.asyncio
async def test_dashboard_does_not_fallback_for_missing_api_asset_or_unknown_page(
    dashboard: ManagementService,
) -> None:
    async with ClientSession() as client:
        for path in (
            "/api/missing",
            "/api/snapshot/other",
            "/unknown",
            "/agents/other",
            "/overview/other",
            "/traces/short",
            "/traces/" + "A" * 32,
            "/traces/" + "0" * 32,
            "/assets/missing.js",
            "/assets/dashboard.js/other",
            "/assets/%2e%2e/management.py",
            "/assets/%2fetc%2fpasswd",
            "/dashboard.html",
        ):
            async with client.get(URL(dashboard.url + path, encoded=True)) as response:
                assert response.status == 404, path
                assert await response.read() != _INDEX


@pytest.mark.asyncio
async def test_operational_api_routes_keep_their_authentication(
    dashboard: ManagementService, monkeypatch: pytest.MonkeyPatch
) -> None:
    denied_routes: list[str] = []

    async def security_event(
        event_type: str,
        details: dict[str, str],
        *,
        instrumented: bool = True,
    ) -> str:
        assert event_type == "MANAGEMENT_ACCESS_DENIED"
        assert details["operator_id"] == "anonymous"
        denied_routes.append(details["route"])
        return "1-0"

    monkeypatch.setattr(dashboard._audit, "log_security_event", security_event)
    routes = (
        "/api/snapshot",
        "/api/history",
        "/api/traces",
        "/api/traces/" + "a" * 32,
        "/healthz",
    )
    async with ClientSession() as client:
        for path in routes:
            async with client.get(dashboard.url + path) as response:
                assert response.status == 401
                assert response.headers["WWW-Authenticate"] == "Bearer"
                assert response.content_type != "text/html"
    assert denied_routes == list(routes)


@pytest.mark.asyncio
async def test_missing_bundle_fails_startup_and_releases_listener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(management_module, "files", lambda package: tmp_path)
    redis = Redis.from_url("redis://127.0.0.1:1", decode_responses=True)
    service = ManagementService(
        settings=ManagementSettings(port=0),
        redis=redis,
        sessions=SessionManager(agents={}, redis=redis),
        agents={},
        gateway=GatewaySettings(),
        audit=AuditModule(redis, file_sink=None),
        circuit_breaker=None,
        is_running=lambda: True,
    )
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

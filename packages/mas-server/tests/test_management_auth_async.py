"""OIDC CA bootstrap preserves async refresh and provider-session ownership."""

from __future__ import annotations

import asyncio
import base64
import json
import ssl
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass
from types import TracebackType

import pytest
from aiohttp import ClientSession, ClientTimeout, TCPConnector
from cryptography.hazmat.primitives.asymmetric import ec
from mas_server.management_auth import (
    OidcAuthenticator,
    OidcSettings,
    OperatorAuthenticationError,
)

pytestmark = pytest.mark.asyncio


@dataclass(slots=True)
class JwksContent:
    body: bytes

    async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
        for start in range(0, len(self.body), size):
            yield self.body[start : start + size]


@dataclass(slots=True)
class JwksResponse:
    content: JwksContent
    status: int = 200

    async def __aenter__(self) -> JwksResponse:
        return self

    async def __aexit__(
        self,
        error_type: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        pass


def _authenticator(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[OidcAuthenticator, list[ClientSession], list[int]]:
    numbers = ec.generate_private_key(ec.SECP256R1()).public_key().public_numbers()
    key = {"kty": "EC", "kid": "key", "crv": "P-256", "alg": "ES256"}
    for name, number in (("x", numbers.x), ("y", numbers.y)):
        key[name] = (
            base64.urlsafe_b64encode(number.to_bytes(32, "big"))
            .decode("ascii")
            .rstrip("=")
        )
    body = json.dumps({"keys": [key]}).encode()
    created: list[ClientSession] = []
    threads: list[int] = []

    def session(*, timeout: ClientTimeout, connector: TCPConnector) -> ClientSession:
        threads.append(threading.get_ident())
        result = ClientSession(timeout=timeout, connector=connector)
        created.append(result)
        return result

    def get(session: ClientSession, url: str, *, allow_redirects: bool) -> JwksResponse:
        assert not allow_redirects
        assert url == "https://provider.test/keys"
        return JwksResponse(JwksContent(body))

    monkeypatch.setattr("mas_server.management_auth.ClientSession", session)
    monkeypatch.setattr(ClientSession, "get", get)
    return (
        OidcAuthenticator(
            OidcSettings(
                issuer="https://provider.test",
                audience="mas-management",
                jwks_url="https://provider.test/keys",
                algorithms=("ES256",),
                ca_cert_path="configured-ca.pem",
                refresh_cooldown_seconds=0.01,
            )
        ),
        created,
        threads,
    )


async def test_slow_ca_load_allows_heartbeat_and_coalesces_provider_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    released = threading.Event()
    calls: list[int] = []
    loop_thread = threading.get_ident()
    stopped = asyncio.Event()
    ticks = 0

    def context(*, cafile: str | None) -> ssl.SSLContext:
        assert cafile == "configured-ca.pem"
        calls.append(threading.get_ident())
        started.set()
        if not released.wait(1):
            raise TimeoutError("test CA bootstrap did not finish")
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    async def heartbeat() -> None:
        nonlocal ticks
        while not stopped.is_set():
            ticks += 1
            await asyncio.sleep(0)

    monkeypatch.setattr(ssl, "create_default_context", context)
    authenticator, created, threads = _authenticator(monkeypatch)
    beat = asyncio.create_task(heartbeat())
    refreshes = [asyncio.create_task(authenticator._refresh("key")) for _ in range(8)]
    try:
        assert await asyncio.to_thread(started.wait, 0.5)
        before = ticks
        await asyncio.sleep(0.005)
        assert ticks > before
        assert not created
        assert authenticator._lock.locked()
        released.set()
        await asyncio.gather(*refreshes)
        assert len(calls) == 1
        assert calls[0] != loop_thread
        assert len(created) == 1
        assert threads == [loop_thread]
        assert "key" in authenticator._keys
    finally:
        released.set()
        stopped.set()
        await asyncio.gather(*refreshes, beat, return_exceptions=True)
        await authenticator.close()
    assert created[0].closed


async def test_canceled_ca_bootstrap_creates_no_session_and_allows_later_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    released = threading.Event()
    finished = threading.Event()
    calls = 0

    def context(*, cafile: str | None) -> ssl.SSLContext:
        nonlocal calls
        calls += 1
        started.set()
        if not released.wait(1):
            raise TimeoutError("test CA bootstrap did not finish")
        result = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        finished.set()
        return result

    monkeypatch.setattr(ssl, "create_default_context", context)
    authenticator, created, _threads = _authenticator(monkeypatch)
    refresh = asyncio.create_task(authenticator._refresh("key"))
    try:
        assert await asyncio.to_thread(started.wait, 0.5)
        refresh.cancel()
        with pytest.raises(asyncio.CancelledError):
            await refresh
        assert not created
        assert authenticator._session is None
        assert not authenticator._lock.locked()
        released.set()
        assert await asyncio.to_thread(finished.wait, 0.5)
        await asyncio.sleep(0.015)
        await authenticator._refresh("key")
        assert calls == 2
        assert len(created) == 1
    finally:
        released.set()
        refresh.cancel()
        await asyncio.gather(refresh, return_exceptions=True)
        await authenticator.close()
    assert created[0].closed


async def test_close_during_ca_bootstrap_waits_then_closes_created_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    released = threading.Event()

    def context(*, cafile: str | None) -> ssl.SSLContext:
        started.set()
        if not released.wait(1):
            raise TimeoutError("test CA bootstrap did not finish")
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    monkeypatch.setattr(ssl, "create_default_context", context)
    authenticator, created, _threads = _authenticator(monkeypatch)
    refresh = asyncio.create_task(authenticator._refresh("key"))
    closing: asyncio.Task[None] | None = None
    try:
        assert await asyncio.to_thread(started.wait, 0.5)
        closing = asyncio.create_task(authenticator.close())
        await asyncio.sleep(0.005)
        assert not closing.done()
        released.set()
        await refresh
        await closing
        assert len(created) == 1
        assert created[0].closed
        assert authenticator._session is None
    finally:
        released.set()
        await asyncio.gather(refresh, return_exceptions=True)
        if closing is not None:
            await closing
        await authenticator.close()


async def test_ca_loading_failure_preserves_stable_fail_closed_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def context(*, cafile: str | None) -> ssl.SSLContext:
        raise OSError("private CA path must not appear in operator rejection")

    monkeypatch.setattr(ssl, "create_default_context", context)
    authenticator, created, _threads = _authenticator(monkeypatch)
    try:
        with pytest.raises(OperatorAuthenticationError) as failure:
            await authenticator._refresh("key")
        assert failure.value.reason == "identity_provider_unavailable"
        assert failure.value.identity is None
        assert not created
        assert authenticator._session is None
        assert not authenticator._lock.locked()
    finally:
        await authenticator.close()

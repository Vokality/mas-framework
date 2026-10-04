"""Signed operator access, key rotation and secret-free access auditing."""

from __future__ import annotations

import asyncio
import base64
import json
import ssl
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

import jwt
import pytest
from aiohttp import ClientSession, web
from cryptography.hazmat.primitives.asymmetric import rsa
from mas_core.protocol import JsonObject
from mas_gateway.audit import AuditModule
from mas_gateway.config import GatewaySettings
from mas_server.management import (
    ManagementService,
    ManagementSettings,
    ManagementTlsSettings,
    TelemetryIngestSettings,
)
from mas_server.management_auth import (
    Algorithm,
    OidcAuthenticator,
    OidcSettings,
    OperatorAuthenticationError,
)
from mas_server.sessions import SessionManager
from redis.asyncio import Redis

from conftest import TestTlsPaths as TlsPaths


@dataclass(slots=True)
class Provider:
    issuer: str
    key: rsa.RSAPrivateKey
    keys: list[JsonObject]
    requests: int = 0

    def token(
        self,
        *,
        kid: str | None = "initial",
        changes: dict[str, object] | None = None,
        algorithm: Algorithm = "RS256",
    ) -> str:
        claims = {
            "sub": "operator-42",
            "iss": self.issuer,
            "aud": "mas-management",
            "iat": time.time(),
            "exp": time.time() + 60,
            "scope": "mas:read",
            "roles": ["operator"],
            **(changes or {}),
        }
        return jwt.encode(
            claims,
            self.key,
            algorithm=algorithm,
            headers={"kid": kid} if kid is not None else {},
        )

    def settings(self, **changes: object) -> OidcSettings:
        return OidcSettings.model_validate(
            {
                "issuer": self.issuer,
                "audience": "mas-management",
                "jwks_url": self.issuer + "/keys",
                "allowed_roles": ["operator"],
                **changes,
            }
        )


@pytest.fixture
async def provider() -> AsyncIterator[Provider]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()
    jwk: JsonObject = {"kty": "RSA"}
    for name, value in (("n", numbers.n), ("e", numbers.e)):
        jwk[name] = (
            base64.urlsafe_b64encode(
                value.to_bytes((value.bit_length() + 7) // 8, "big")
            )
            .decode("ascii")
            .rstrip("=")
        )
    jwk.update({"kid": "initial", "alg": "RS256", "use": "sig"})
    state = Provider("", key, [jwk])

    async def keys(request: web.Request) -> web.Response:
        state.requests += 1
        return web.json_response({"keys": state.keys})

    app = web.Application()
    app.router.add_get("/keys", keys)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    state.issuer = f"http://127.0.0.1:{runner.addresses[0][1]}"
    try:
        yield state
    finally:
        await runner.cleanup()


async def test_signed_operator_identity_and_coalesced_cache(provider: Provider) -> None:
    authenticator = OidcAuthenticator(provider.settings())
    try:
        identities = await asyncio.gather(
            *(
                authenticator.authenticate("Bearer " + provider.token())
                for _ in range(20)
            )
        )
        assert all(identity.subject == "operator-42" for identity in identities)
        assert provider.requests == 1
    finally:
        await authenticator.close()


async def test_optional_jwk_algorithm_uses_configured_pin(provider: Provider) -> None:
    del provider.keys[0]["alg"]
    authenticator = OidcAuthenticator(provider.settings(algorithms=["RS384"]))
    try:
        identity = await authenticator.authenticate(
            "Bearer " + provider.token(algorithm="RS384")
        )
        assert identity.subject == "operator-42"
    finally:
        await authenticator.close()


async def test_optional_key_id_requires_one_unambiguous_signing_key(
    provider: Provider,
) -> None:
    del provider.keys[0]["kid"]
    authenticator = OidcAuthenticator(provider.settings())
    try:
        assert (
            await authenticator.authenticate("Bearer " + provider.token(kid=None))
        ).subject == "operator-42"
    finally:
        await authenticator.close()
    provider.keys[0]["kid"] = "first"
    second = dict(provider.keys[0])
    second["kid"] = "second"
    provider.keys.append(second)
    authenticator = OidcAuthenticator(provider.settings())
    try:
        with pytest.raises(OperatorAuthenticationError, match="invalid_token"):
            await authenticator.authenticate("Bearer " + provider.token(kid=None))
    finally:
        await authenticator.close()


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"exp": 0}, "invalid_token"),
        ({"aud": "other"}, "invalid_token"),
        ({"iss": "https://other.example"}, "invalid_token"),
        ({"iat": time.time() + 3600}, "invalid_token"),
        ({"sub": ""}, "invalid_token"),
        ({"scope": "other"}, "insufficient_access"),
        ({"roles": ["viewer"]}, "insufficient_access"),
    ],
)
async def test_registered_claims_and_explicit_permissions(
    provider: Provider, changes: dict[str, object], reason: str
) -> None:
    authenticator = OidcAuthenticator(provider.settings())
    try:
        with pytest.raises(OperatorAuthenticationError) as failure:
            await authenticator.authenticate(
                "Bearer " + provider.token(changes=changes)
            )
        assert failure.value.reason == reason
        assert (failure.value.identity is not None) == (reason == "insufficient_access")
    finally:
        await authenticator.close()


async def test_signature_is_required_and_unknown_kid_cannot_amplify_network(
    provider: Provider,
) -> None:
    authenticator = OidcAuthenticator(provider.settings())
    try:
        await authenticator.authenticate("Bearer " + provider.token())
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        forged = jwt.encode(
            {
                "iss": provider.issuer,
                "aud": "mas-management",
                "sub": "secret-claim",
                "iat": time.time(),
                "exp": time.time() + 60,
            },
            other,
            algorithm="RS256",
            headers={"kid": "initial"},
        )
        with pytest.raises(
            OperatorAuthenticationError, match="invalid_token"
        ) as failure:
            await authenticator.authenticate("Bearer " + forged)
        assert failure.value.identity is None
        for index in range(10):
            with pytest.raises(OperatorAuthenticationError, match="invalid_token"):
                await authenticator.authenticate(
                    "Bearer " + provider.token(kid=f"random-{index}")
                )
        assert provider.requests == 1
    finally:
        await authenticator.close()


async def test_expired_cache_removes_revoked_signing_keys(provider: Provider) -> None:
    authenticator = OidcAuthenticator(provider.settings(cache_seconds=0.01))
    try:
        await authenticator.authenticate("Bearer " + provider.token())
        replacement = dict(provider.keys[0])
        replacement["kid"] = "rotated"
        provider.keys = [replacement]
        await asyncio.sleep(0.02)
        with pytest.raises(OperatorAuthenticationError, match="invalid_token"):
            await authenticator.authenticate("Bearer " + provider.token())
        assert (
            await authenticator.authenticate("Bearer " + provider.token(kid="rotated"))
        ).subject == "operator-42"
        assert provider.requests == 2
    finally:
        await authenticator.close()


async def test_oversized_jwks_fails_closed_with_refresh_cooldown(
    provider: Provider,
) -> None:
    provider.keys *= 200
    authenticator = OidcAuthenticator(provider.settings())
    try:
        for _ in range(2):
            with pytest.raises(
                OperatorAuthenticationError, match="identity_provider_unavailable"
            ):
                await authenticator.authenticate("Bearer " + provider.token())
        assert provider.requests == 1
    finally:
        await authenticator.close()


async def test_unpinned_symmetric_algorithm_rejected_before_network(
    provider: Provider,
) -> None:
    authenticator = OidcAuthenticator(provider.settings())
    token = jwt.encode(
        {"sub": "untrusted"}, "x" * 32, algorithm="HS256", headers={"kid": "initial"}
    )
    try:
        with pytest.raises(OperatorAuthenticationError, match="invalid_token"):
            await authenticator.authenticate("Bearer " + token)
        assert provider.requests == 0
    finally:
        await authenticator.close()


async def test_https_and_per_operator_access_audit(
    redis: Redis, provider: Provider, test_tls: TlsPaths
) -> None:
    audit = AuditModule(redis, file_sink=None)
    service = ManagementService(
        settings=ManagementSettings(
            port=0,
            auth_mode="oidc",
            oidc=provider.settings(),
            tls=ManagementTlsSettings(test_tls.server_cert, test_tls.server_key),
        ),
        redis=redis,
        sessions=SessionManager(agents={}, redis=redis),
        agents={},
        gateway=GatewaySettings(),
        audit=audit,
        circuit_breaker=None,
        is_running=lambda: True,
    )
    await service.start()
    try:
        assert service.url.startswith("https://")
        context = ssl.create_default_context(cafile=test_tls.ca_pem)
        bearer = provider.token()
        async with ClientSession() as client:
            async with client.get(
                service.url + "/api/snapshot",
                ssl=context,
                headers={"Authorization": "Bearer " + bearer},
            ) as response:
                assert response.status == 200
            async with client.get(
                service.url + "/healthz",
                ssl=context,
                headers={
                    "Authorization": "Bearer "
                    + provider.token(changes={"scope": "other"})
                },
            ) as response:
                assert response.status == 403
            async with client.get(
                service.url + "/healthz",
                ssl=context,
                headers={"Authorization": "Bearer invalid-secret"},
            ) as response:
                assert response.status == 401
        events = await audit.query_security_events()
        assert [entry["event_type"] for entry in events] == [
            "MANAGEMENT_ACCESS_ALLOWED",
            "MANAGEMENT_ACCESS_DENIED",
            "MANAGEMENT_ACCESS_DENIED",
        ]
        assert events[0]["details"] == {
            "operator_id": "operator-42",
            "issuer": provider.issuer,
            "route": "/api/snapshot",
            "reason": "authorized",
        }
        assert "invalid-secret" not in json.dumps(events) and bearer not in json.dumps(
            events
        )
        assert events[-1]["details"] == {
            "operator_id": "anonymous",
            "issuer": "unknown",
            "route": "/healthz",
            "reason": "invalid_token",
        }
    finally:
        await service.stop()


async def test_oidc_reader_scope_cannot_ingest_telemetry(
    redis: Redis, provider: Provider
) -> None:
    service = ManagementService(
        settings=ManagementSettings(
            port=0,
            auth_mode="oidc",
            oidc=provider.settings(),
            telemetry_ingest=TelemetryIngestSettings(
                auth_mode="oidc",
                oidc=provider.settings(required_scopes=["mas:telemetry:write"]),
            ),
        ),
        redis=redis,
        sessions=SessionManager(agents={}, redis=redis),
        agents={},
        gateway=GatewaySettings(),
        audit=AuditModule(redis, file_sink=None),
        circuit_breaker=None,
        is_running=lambda: True,
    )
    await service.start()
    try:
        async with ClientSession() as client:
            for scope, status in (("mas:read", 403), ("mas:telemetry:write", 200)):
                async with client.post(
                    service.url + "/v1/traces",
                    data=b"",
                    headers={
                        "Authorization": "Bearer "
                        + provider.token(changes={"scope": scope}),
                        "Content-Type": "application/x-protobuf",
                    },
                ) as response:
                    assert response.status == status
            async with client.get(
                service.url + "/api/history",
                headers={
                    "Authorization": "Bearer "
                    + provider.token(changes={"scope": "mas:telemetry:write"})
                },
            ) as response:
                assert response.status == 403
        events = await service._audit.query_security_events()
        assert [entry["event_type"] for entry in events] == [
            "TELEMETRY_INGEST_DENIED",
            "TELEMETRY_INGEST_ALLOWED",
            "MANAGEMENT_ACCESS_DENIED",
        ]
    finally:
        await service.stop()


def test_remote_management_requires_both_oidc_and_https() -> None:
    provider = OidcSettings(
        issuer="https://idp.example",
        audience="mas",
        jwks_url="https://idp.example/keys",
    )
    with pytest.raises(ValueError, match="OIDC and HTTPS"):
        ManagementSettings(host="0.0.0.0", auth_mode="oidc", oidc=provider)
    settings = ManagementSettings(
        host="0.0.0.0",
        auth_mode="oidc",
        oidc=provider,
        tls=ManagementTlsSettings("cert.pem", "key.pem"),
    )
    assert settings.oidc == provider


def test_plaintext_external_provider_and_missing_permissions_are_rejected() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        OidcSettings(
            issuer="http://external.example",
            audience="mas",
            jwks_url="http://external.example/keys",
        )
    with pytest.raises(ValueError, match="explicit scopes or roles"):
        OidcSettings(
            issuer="https://idp.example",
            audience="mas",
            jwks_url="https://idp.example/keys",
            required_scopes=frozenset(),
        )

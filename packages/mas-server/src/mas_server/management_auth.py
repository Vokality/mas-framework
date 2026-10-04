"""Provider-neutral signed bearer validation for management operators."""

from __future__ import annotations

import asyncio
import ipaddress
import ssl
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

import jwt
from aiohttp import ClientError, ClientSession, ClientTimeout, TCPConnector
from mas_core.protocol import JsonObject, validate_json_object
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

Algorithm = Literal["RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "EdDSA"]
_STRINGS = TypeAdapter(list[str])
_KEYS = TypeAdapter(list[JsonObject])


class OidcSettings(BaseModel):
    """Pinned identity provider and explicit read permission requirements."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    issuer: str = Field(min_length=1)
    audience: str = Field(min_length=1)
    jwks_url: str
    algorithms: tuple[Algorithm, ...] = ("RS256",)
    required_scopes: frozenset[str] = frozenset({"mas:read"})
    allowed_roles: frozenset[str] = frozenset()
    scope_claim: str = "scope"
    roles_claim: str = "roles"
    timeout_seconds: float = Field(default=3, gt=0, le=30, allow_inf_nan=False)
    cache_seconds: float = Field(default=300, gt=0, le=3600, allow_inf_nan=False)
    refresh_cooldown_seconds: float = Field(
        default=5, gt=0, le=300, allow_inf_nan=False
    )
    ca_cert_path: str | None = None

    @model_validator(mode="after")
    def _validate_provider(self) -> OidcSettings:
        """Require TLS endpoints except for an explicitly local provider."""
        for address in (self.issuer, self.jwks_url):
            parsed = urlsplit(address)
            if (
                parsed.username
                or parsed.password
                or parsed.fragment
                or not parsed.hostname
            ):
                raise ValueError(
                    "OIDC endpoints require a host without credentials or fragments"
                )
            local = parsed.hostname == "localhost"
            with suppress(ValueError):
                local = local or ipaddress.ip_address(parsed.hostname).is_loopback
            if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
                raise ValueError("OIDC endpoints require HTTPS except on loopback")
        if not self.algorithms or not (self.required_scopes or self.allowed_roles):
            raise ValueError(
                "OIDC requires signing algorithms and explicit scopes or roles"
            )
        if not self.scope_claim or not self.roles_claim:
            raise ValueError("OIDC permission claim names cannot be empty")
        return self


@dataclass(frozen=True, slots=True)
class OperatorIdentity:
    """Only authenticated identity data is available to audit callers."""

    subject: str
    issuer: str


class OperatorAuthenticationError(Exception):
    """Stable public rejection without bearer data or provider exception text."""

    def __init__(
        self,
        reason: Literal[
            "missing_bearer",
            "invalid_token",
            "insufficient_access",
            "identity_provider_unavailable",
        ],
        identity: OperatorIdentity | None = None,
    ) -> None:
        self.reason = reason
        self.identity = identity
        super().__init__(reason)


class _Header(BaseModel):
    kid: str | None = Field(default=None, min_length=1, max_length=128)
    alg: Algorithm


class _Claims(BaseModel):
    sub: str = Field(min_length=1, max_length=256)
    iss: str
    exp: float = Field(allow_inf_nan=False)
    iat: float = Field(allow_inf_nan=False)


class OidcAuthenticator:
    """Bounded asynchronous JWKS cache with coalesced key rotation refreshes."""

    def __init__(self, settings: OidcSettings) -> None:
        self._settings = settings
        self._keys: dict[str, dict[Algorithm, jwt.PyJWK]] = {}
        self._expires_at = 0.0
        self._refreshed_at = float("-inf")
        self._lock = asyncio.Lock()
        self._session: ClientSession | None = None

    async def close(self) -> None:
        """Release the provider connection pool."""
        async with self._lock:
            if self._session is not None:
                await self._session.close()
                self._session = None

    async def authenticate(self, authorization: str) -> OperatorIdentity:
        """Verify signature and registered claims before examining permissions."""
        if not authorization.startswith("Bearer "):
            raise OperatorAuthenticationError("missing_bearer")
        token = authorization[7:]
        if not token or len(token) > 8192:
            raise OperatorAuthenticationError("invalid_token")
        try:
            header = _Header.model_validate(jwt.get_unverified_header(token))
            if header.alg not in self._settings.algorithms:
                raise OperatorAuthenticationError("invalid_token")
            await self._refresh(header.kid)
            if header.kid is None:
                candidates = [
                    variants[header.alg]
                    for variants in self._keys.values()
                    if header.alg in variants
                ]
                key = candidates[0] if len(candidates) == 1 else None
            else:
                key = self._keys.get(header.kid, {}).get(header.alg)
            if key is None:
                raise OperatorAuthenticationError("invalid_token")
            decoded: object = jwt.decode(
                token,
                key=key,
                algorithms=list(self._settings.algorithms),
                issuer=self._settings.issuer,
                audience=self._settings.audience,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
            raw = validate_json_object(decoded)
            claims = _Claims.model_validate(raw, strict=True)
            identity = OperatorIdentity(claims.sub, claims.iss)
            scopes = self._permission_values(raw.get(self._settings.scope_claim))
            roles = self._permission_values(raw.get(self._settings.roles_claim))
            if not self._settings.required_scopes.issubset(scopes) or (
                self._settings.allowed_roles
                and not self._settings.allowed_roles.intersection(roles)
            ):
                raise OperatorAuthenticationError("insufficient_access", identity)
            return identity
        except (jwt.PyJWTError, ValidationError, ValueError, TypeError) as exc:
            raise OperatorAuthenticationError("invalid_token") from exc

    @staticmethod
    def _permission_values(raw: object) -> set[str]:
        if raw is None:
            return set()
        if isinstance(raw, str):
            return set(raw.split())
        return set(_STRINGS.validate_python(raw, strict=True))

    async def _refresh(self, kid: str | None) -> None:
        now = time.monotonic()
        if now < self._expires_at and (
            kid is None
            or kid in self._keys
            or now - self._refreshed_at < self._settings.refresh_cooldown_seconds
        ):
            return
        async with self._lock:
            now = time.monotonic()
            if not self._keys and (
                now - self._refreshed_at < self._settings.refresh_cooldown_seconds
            ):
                raise OperatorAuthenticationError("identity_provider_unavailable")
            if now < self._expires_at and (
                kid is None
                or kid in self._keys
                or now - self._refreshed_at < self._settings.refresh_cooldown_seconds
            ):
                return
            self._refreshed_at = now
            try:
                if self._session is None:
                    context = await asyncio.to_thread(
                        ssl.create_default_context, cafile=self._settings.ca_cert_path
                    )
                    self._session = ClientSession(
                        timeout=ClientTimeout(total=self._settings.timeout_seconds),
                        connector=TCPConnector(ssl=context, limit=2, limit_per_host=2),
                    )
                async with self._session.get(
                    self._settings.jwks_url, allow_redirects=False
                ) as response:
                    if response.status != 200:
                        raise ValueError("JWKS unavailable")
                    body = bytearray()
                    async for chunk in response.content.iter_chunked(8192):
                        body.extend(chunk)
                        if len(body) > 65536:
                            raise ValueError("JWKS exceeds bound")
                raw = TypeAdapter(JsonObject).validate_json(bytes(body))
                entries = _KEYS.validate_python(raw.get("keys"), strict=True)
                if not entries or len(entries) > 100:
                    raise ValueError("JWKS key count exceeds bound")
                keys: dict[str, dict[Algorithm, jwt.PyJWK]] = {}
                for entry in entries:
                    if entry.get("kty") not in {"RSA", "EC", "OKP"} or entry.get(
                        "use"
                    ) not in (None, "sig"):
                        continue
                    if any(name in entry for name in ("d", "p", "q", "dp", "dq", "qi")):
                        raise ValueError("JWKS must contain public keys")
                    if "key_ops" in entry and "verify" not in _STRINGS.validate_python(
                        entry["key_ops"], strict=True
                    ):
                        continue
                    algorithms = [
                        algorithm
                        for algorithm in self._settings.algorithms
                        if entry.get("alg") in (None, algorithm)
                        and (
                            (entry.get("kty") == "RSA" and algorithm.startswith("RS"))
                            or (
                                entry.get("kty") == "EC"
                                and entry.get("crv")
                                == {
                                    "ES256": "P-256",
                                    "ES384": "P-384",
                                    "ES512": "P-521",
                                }.get(algorithm)
                            )
                            or (
                                entry.get("kty") == "OKP"
                                and algorithm == "EdDSA"
                                and entry.get("crv") in ("Ed25519", "Ed448")
                            )
                        )
                    ]
                    if not algorithms:
                        continue
                    parsed = {
                        algorithm: jwt.PyJWK.from_dict(entry, algorithm=algorithm)
                        for algorithm in algorithms
                    }
                    key = parsed[algorithms[0]]
                    key_id = key.key_id
                    if key_id is None:
                        key_id = ""
                    if (
                        not isinstance(key_id, str)
                        or len(key_id) > 128
                        or key_id in keys
                    ):
                        raise ValueError("JWKS requires unique key identifiers")
                    keys[key_id] = parsed
                if not keys:
                    raise ValueError("JWKS has no permitted signing keys")
                self._keys = keys
                self._expires_at = time.monotonic() + self._settings.cache_seconds
            except (
                ClientError,
                OSError,
                TimeoutError,
                ValueError,
                TypeError,
                jwt.PyJWTError,
                ValidationError,
            ) as exc:
                self._keys = {}
                self._expires_at = 0
                raise OperatorAuthenticationError(
                    "identity_provider_unavailable"
                ) from exc

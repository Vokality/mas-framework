"""mTLS identity parsing for MAS server."""

from __future__ import annotations

import asyncio
import re
import threading
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from functools import lru_cache
from typing import TYPE_CHECKING, Protocol
from weakref import WeakKeyDictionary

import grpc
from cryptography import x509
from cryptography.x509.verification import VerificationError

from .errors import RpcError, UnauthenticatedError
from .peer_validation import PeerValidationCapacityError, QueuedPeerValidator
from .tls import PeerCertificatePolicy

if TYPE_CHECKING:
    from .types import TlsConfig

_SPIFFE_RE = re.compile(r"^spiffe://mas/agent/(?P<agent_id>[a-zA-Z0-9_-]{1,128})$")


class PeerAuthenticationContext(Protocol):
    """Authenticated peer properties supplied by the transport."""

    def auth_context(self) -> Mapping[str, Iterable[bytes]]:
        """Return the current call's verified peer properties."""
        ...


@lru_cache(maxsize=32)
def _peer_policy(tls: TlsConfig) -> PeerCertificatePolicy:
    return PeerCertificatePolicy(tls)


_VALIDATORS: WeakKeyDictionary[
    asyncio.AbstractEventLoop, OrderedDict[TlsConfig, QueuedPeerValidator]
] = WeakKeyDictionary()
_VALIDATORS_LOCK = threading.Lock()


def _peer_validator(tls: TlsConfig) -> QueuedPeerValidator:
    loop = asyncio.get_running_loop()
    with _VALIDATORS_LOCK:
        validators = _VALIDATORS.get(loop)
        if validators is None:
            if len(_VALIDATORS) >= 256:
                raise PeerValidationCapacityError(
                    "Peer validation registry capacity exceeded"
                )
            validators = OrderedDict()
            _VALIDATORS[loop] = validators
        validator = validators.get(tls)
        if validator is None:
            if len(validators) >= 32:
                idle = next(
                    (key for key, value in validators.items() if value.idle),
                    None,
                )
                if idle is None:
                    raise PeerValidationCapacityError(
                        "Peer validation registry capacity exceeded"
                    )
                del validators[idle]
            validator = QueuedPeerValidator(_peer_policy(tls))
            validators[tls] = validator
        validators.move_to_end(tls)
        return validator


async def spiffe_agent_id(
    context: PeerAuthenticationContext, *, tls: TlsConfig | None = None
) -> str:
    """Validate current peer trust in a worker and extract its mTLS SPIFFE SAN."""
    auth_ctx = context.auth_context() or {}
    if tls is not None:
        certificates = list(auth_ctx.get("x509_pem_cert", []))
        chains = list(auth_ctx.get("x509_pem_cert_chain", []))
        if len(certificates) != 1 or not isinstance(certificates[0], bytes):
            raise UnauthenticatedError("missing_peer_certificate")
        if any(not isinstance(chain, bytes) for chain in chains):
            raise UnauthenticatedError("invalid_peer_certificate")
        try:
            await _peer_validator(tls).validate(certificates[0], b"".join(chains))
        except PeerValidationCapacityError as exc:
            raise RpcError(
                grpc.StatusCode.RESOURCE_EXHAUSTED, "peer_validation_capacity"
            ) from exc
        except (OSError, ValueError, VerificationError, x509.ExtensionNotFound) as exc:
            raise UnauthenticatedError("peer_certificate_rejected") from exc
    sans = auth_ctx.get("x509_subject_alternative_name")
    if not sans:
        raise UnauthenticatedError("missing_spiffe_san")

    spiffes: list[str] = []
    for raw in sans:
        if isinstance(raw, bytes):
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
        else:
            text = str(raw)

        if text.startswith("spiffe://"):
            spiffes.append(text)

    if len(spiffes) != 1:
        raise UnauthenticatedError("invalid_spiffe_san")

    match = _SPIFFE_RE.match(spiffes[0])
    if not match:
        raise UnauthenticatedError("invalid_spiffe_format")

    return match.group("agent_id")

"""mTLS identity parsing for MAS server."""

from __future__ import annotations

import re

import grpc.aio as grpc_aio

from .errors import UnauthenticatedError

_SPIFFE_RE = re.compile(r"^spiffe://mas/agent/(?P<agent_id>[a-zA-Z0-9_-]{1,64})$")


def spiffe_agent_id(context: grpc_aio.ServicerContext) -> str:
    """Extract agent ID from mTLS SPIFFE SAN."""
    auth_ctx = context.auth_context() or {}
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

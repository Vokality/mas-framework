"""Static client configuration for the MAS agent."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TlsClientConfig:
    """Client mTLS credential paths."""

    root_ca_path: str
    client_cert_path: str
    client_key_path: str


# Graceful-shutdown budgets. On stop() we let in-flight handlers finish (so
# their ACK/NACK is enqueued) and flush the outgoing queue before cancelling the
# transport, otherwise the server redelivers messages whose work already
# completed.
HANDLER_DRAIN_TIMEOUT = 5.0
OUTGOING_DRAIN_TIMEOUT = 2.0

# Bounds for the early-reply buffer. Replies that arrive after their request has
# already timed out or been cancelled would otherwise accumulate forever, so the
# buffer caps its size (oldest evicted first) and expires entries after a TTL.
EARLY_REPLY_MAX_ENTRIES = 1024
EARLY_REPLY_TTL_SECONDS = 30.0

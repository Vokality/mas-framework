"""Message routing and DLQ handling."""

from __future__ import annotations

import hashlib
import time

from ..protocol import EnvelopeMessage
from ..redis_types import AsyncRedisProtocol


class MessageRouter:
    """Route envelopes into Redis streams and write DLQ entries."""

    def __init__(self, *, redis: AsyncRedisProtocol, dlq_enabled: bool) -> None:
        """Initialize router with Redis connection."""
        self._redis = redis
        self._dlq_enabled = dlq_enabled

    async def route_message(self, message: EnvelopeMessage) -> None:
        """Write message payload to the appropriate Redis stream."""
        envelope_json = message.model_dump_json()
        fields = {"envelope": envelope_json}

        if message.meta.is_reply:
            if not message.meta.reply_to_instance_id:
                from .errors import InvalidArgumentError

                raise InvalidArgumentError("missing_reply_to_instance_id")
            stream_name = (
                f"agent.stream:{message.target_id}:{message.meta.reply_to_instance_id}"
            )
        else:
            stream_name = f"agent.stream:{message.target_id}"

        await self._redis.xadd(stream_name, fields)

    async def write_dlq(self, *, envelope_json: str, reason: str) -> None:
        """Write a message to the DLQ stream for auditing."""
        if not self._dlq_enabled:
            return

        try:
            msg = EnvelopeMessage.model_validate_json(envelope_json)
        except Exception:
            return

        envelope_hash = hashlib.sha256(envelope_json.encode()).hexdigest()
        fields: dict[str, str] = {
            "message_id": msg.message_id,
            "sender_id": msg.sender_id,
            "sender_instance_id": msg.meta.sender_instance_id or "",
            "target_id": msg.target_id,
            "message_type": msg.message_type,
            "decision": "DLQ",
            "reason": reason,
            "envelope_hash": envelope_hash,
            "timestamp": str(time.time()),
        }

        try:
            await self._redis.xadd("dlq:messages", fields)
        except Exception:
            pass

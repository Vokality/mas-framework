"""Message routing and DLQ handling."""

from __future__ import annotations

import hashlib
import logging
import time

from mas_core import EnvelopeMessage, SpanKind, get_telemetry
from redis.asyncio import Redis

from .errors import InvalidArgumentError

logger = logging.getLogger(__name__)


class MessageRouter:
    """Route envelopes into Redis streams and write DLQ entries."""

    def __init__(self, *, redis: Redis[str], dlq_enabled: bool) -> None:
        """Initialize router with Redis connection."""
        self._redis = redis
        self._dlq_enabled = dlq_enabled

    async def route_message(self, message: EnvelopeMessage) -> None:
        """Write message payload to the appropriate Redis stream."""
        telemetry = get_telemetry()
        with telemetry.start_span(
            "mas.server.routing.route_message",
            kind=SpanKind.PRODUCER,
            attributes={
                "mas.target_id": message.target_id,
                "mas.message_type": message.message_type,
                "mas.is_reply": message.meta.is_reply,
            },
        ):
            envelope_json = message.model_dump_json()
            fields = {"envelope": envelope_json}

            if message.meta.is_reply:
                if not message.meta.reply_to_instance_id:
                    raise InvalidArgumentError("missing_reply_to_instance_id")
                stream_name = (
                    f"agent.stream:{message.target_id}:"
                    f"{message.meta.reply_to_instance_id}"
                )
            else:
                stream_name = f"agent.stream:{message.target_id}"

            await self._redis.xadd(stream_name, fields)

    async def write_dlq(self, *, envelope_json: str, reason: str) -> bool:
        """Write a message to the DLQ stream.

        Returns ``True`` when the stream entry is safe to ack: either the write
        succeeded, or DLQ is disabled and the delivery is intentionally dropped.
        Returns ``False`` only when a DLQ write was attempted and failed.
        """
        telemetry = get_telemetry()
        with telemetry.start_span(
            "mas.server.routing.write_dlq",
            kind=SpanKind.PRODUCER,
            attributes={"mas.dlq.reason": reason},
        ):
            if not self._dlq_enabled:
                telemetry.record_dlq_write(result="disabled")
                return True

            envelope_hash = hashlib.sha256(envelope_json.encode()).hexdigest()
            try:
                msg = EnvelopeMessage.model_validate_json(envelope_json)
            except Exception:
                logger.debug(
                    "Failed to parse envelope for DLQ write; writing fallback record",
                    exc_info=True,
                    extra={"reason": reason},
                )
                fields: dict[str, str] = {
                    "message_id": "",
                    "sender_id": "",
                    "sender_instance_id": "",
                    "target_id": "",
                    "message_type": "",
                    "decision": "DLQ",
                    "reason": reason,
                    "envelope_hash": envelope_hash,
                    "timestamp": str(time.time()),
                }
            else:
                fields = {
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
                telemetry.record_dlq_write(result="success")
                return True
            except Exception:
                telemetry.record_dlq_write(result="failed")
                telemetry.record_redis_error(component="routing", operation="xadd_dlq")
                logger.warning(
                    "Failed to write message to DLQ stream",
                    exc_info=True,
                    extra={
                        "message_id": msg.message_id,
                        "reason": reason,
                    },
                )
                return False

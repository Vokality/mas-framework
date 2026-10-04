"""Message routing and DLQ handling."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass

from mas_core import EnvelopeMessage, SpanKind, get_telemetry
from mas_core.durability import RedisDurability
from mas_core.redis_commit import RedisCommitTarget, StreamAppend
from mas_core.sessions import SessionLease
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from redis.asyncio import Redis
from redis.typing import EncodableT, FieldT

from .errors import InvalidArgumentError
from .types import InflightDelivery

logger = logging.getLogger(__name__)


class ReplyReceipt(BaseModel):
    """A committed reply's identity, retained only until its request expires."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sender_id: str = Field(min_length=1)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    message_id: str = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class CorrelationCommit:
    """The unchanged correlation and original deadline required to commit a reply."""

    expected_value: str
    fingerprint: str
    expires_at: float


@dataclass(frozen=True, slots=True)
class DeliveryCommit:
    """The pending work and leased owner required to finish a delivery."""

    inflight: InflightDelivery
    lease: SessionLease


_OPTIONAL_STRING = TypeAdapter(str | None)
_INTEGER = TypeAdapter(int)

_COMMIT_DELIVERY_SCRIPT = """
if redis.call('GET', KEYS[2]) ~= ARGV[4] then return 0 end
local pending = redis.call('XPENDING', KEYS[1], ARGV[1], ARGV[2], ARGV[2], 1)
if #pending == 0 or pending[1][2] ~= ARGV[3] then return 0 end
local enabled = ARGV[5] == '1'
if enabled then
    local kind = redis.call('TYPE', KEYS[3]).ok
    if kind ~= 'none' and kind ~= 'stream' then
        return redis.error_reply('invalid_dlq_stream_type')
    end
    local fields = cjson.decode(ARGV[6])
    if type(fields) ~= 'table' or #fields == 0 or #fields % 2 ~= 0 then
        return redis.error_reply('invalid_dlq_fields')
    end
    for _, value in ipairs(fields) do
        if type(value) ~= 'string' then
            return redis.error_reply('invalid_dlq_fields')
        end
    end
    redis.call('XADD', KEYS[3], '*', unpack(fields))
end
redis.call('XACK', KEYS[1], ARGV[1], ARGV[2])
redis.call('XDEL', KEYS[1], ARGV[2])
return 1
"""

_COMMIT_REPLY_SCRIPT = """
local receipt_type = redis.call('TYPE', KEYS[3]).ok
if receipt_type ~= 'none' and receipt_type ~= 'string' then
    return redis.error_reply('invalid_reply_receipt_type')
end
local completed = redis.call('GET', KEYS[3])
if completed then
    local receipt = cjson.decode(completed)
    if type(receipt) ~= 'table' or type(receipt.message_id) ~= 'string'
        or receipt.message_id == '' or type(receipt.sender_id) ~= 'string'
        or type(receipt.fingerprint) ~= 'string' then
        return redis.error_reply('invalid_reply_receipt')
    end
    if receipt.sender_id ~= ARGV[4] or receipt.fingerprint ~= ARGV[5] then
        return false
    end
    redis.call('SET', KEYS[3], completed, 'KEEPTTL')
    return receipt.message_id
end
local pending_type = redis.call('TYPE', KEYS[1]).ok
if pending_type ~= 'none' and pending_type ~= 'string' then
    return redis.error_reply('invalid_correlation_type')
end
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
    return false
end
local remaining = redis.call('PTTL', KEYS[1])
local now = redis.call('TIME')
local current_ms = now[1] * 1000 + math.floor(now[2] / 1000)
local deadline_remaining = tonumber(ARGV[6]) - current_ms
remaining = math.min(remaining, deadline_remaining)
if remaining <= 0 then
    return false
end
local stream_type = redis.call('TYPE', KEYS[2]).ok
if stream_type ~= 'none' and stream_type ~= 'stream' then
    return redis.error_reply('invalid_reply_stream_type')
end
local receipt = cjson.decode(ARGV[3])
if type(receipt) ~= 'table' or type(receipt.message_id) ~= 'string'
    or receipt.message_id == '' or type(receipt.sender_id) ~= 'string'
    or type(receipt.fingerprint) ~= 'string' then
    return redis.error_reply('invalid_reply_receipt')
end
redis.call('XADD', KEYS[2], '*', 'envelope', ARGV[2])
redis.call('SET', KEYS[3], ARGV[3], 'PX', remaining)
redis.call('DEL', KEYS[1])
return receipt.message_id
"""

_REPLAY_REPLY_SCRIPT = """
local receipt_type = redis.call('TYPE', KEYS[1]).ok
if receipt_type ~= 'none' and receipt_type ~= 'string' then
    return redis.error_reply('invalid_reply_receipt_type')
end
local completed = redis.call('GET', KEYS[1])
if not completed then
    return false
end
local receipt = cjson.decode(completed)
if type(receipt) ~= 'table' or type(receipt.message_id) ~= 'string'
    or receipt.message_id == '' or type(receipt.sender_id) ~= 'string'
    or type(receipt.fingerprint) ~= 'string' then
    return redis.error_reply('invalid_reply_receipt')
end
if receipt.sender_id ~= ARGV[1] or receipt.fingerprint ~= ARGV[2] then
    return false
end
redis.call('SET', KEYS[1], completed, 'KEEPTTL')
return receipt.message_id
"""


class MessageRouter:
    """Route envelopes into Redis streams and write DLQ entries."""

    def __init__(
        self,
        *,
        redis: Redis,
        dlq_enabled: bool,
        durability: RedisDurability | None = None,
    ) -> None:
        """Initialize router with Redis connection."""
        self._redis = redis
        self._dlq_enabled = dlq_enabled
        self._durability = durability or RedisDurability()

    @property
    def commit_target(self) -> RedisCommitTarget:
        """Describe the backend and confirmation policy required by routing."""
        return RedisCommitTarget(self._redis.connection_pool, self._durability)

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
            append = self.prepare_stream_append(message)
            async with self._redis.pipeline(transaction=False) as pipeline:
                pipeline.xadd(append.stream, dict(append.fields))
                await self._durability.execute(pipeline)

    @staticmethod
    def prepare_stream_append(message: EnvelopeMessage) -> StreamAppend:
        """Describe routing so another durable operation can commit it atomically."""
        if message.meta.is_reply:
            if not message.meta.reply_to_instance_id:
                raise InvalidArgumentError("missing_reply_to_instance_id")
            stream = (
                f"agent.stream:{message.target_id}:{message.meta.reply_to_instance_id}"
            )
        else:
            stream = f"agent.stream:{message.target_id}"
        return StreamAppend(stream, (("envelope", message.model_dump_json()),))

    async def commit_reply(
        self, message: EnvelopeMessage, correlation: CorrelationCommit
    ) -> str:
        """Commit a reply and its correlation receipt in one Redis operation."""
        correlation_id = message.meta.correlation_id
        instance_id = message.meta.reply_to_instance_id
        if not message.meta.is_reply or not correlation_id or not instance_id:
            raise InvalidArgumentError("invalid_reply_envelope")
        receipt = ReplyReceipt(
            sender_id=message.sender_id,
            fingerprint=correlation.fingerprint,
            message_id=message.message_id,
        )
        async with self._redis.client() as connection:
            result = _OPTIONAL_STRING.validate_python(
                await connection.eval(
                    _COMMIT_REPLY_SCRIPT,
                    3,
                    f"mas.pending_request:{correlation_id}",
                    f"agent.stream:{message.target_id}:{instance_id}",
                    f"mas.reply_receipt:{correlation_id}",
                    correlation.expected_value,
                    message.model_dump_json(),
                    receipt.model_dump_json(),
                    message.sender_id,
                    correlation.fingerprint,
                    int(correlation.expires_at * 1000),
                )
            )
            if result is None:
                raise InvalidArgumentError("unknown_or_completed_correlation_id")
            await self._durability.confirm(connection)
            return result

    async def replay_reply(
        self, *, correlation_id: str, sender_id: str, fingerprint: str
    ) -> str | None:
        """Confirm a matching completed reply without enqueueing it again."""
        async with self._redis.client() as connection:
            result = _OPTIONAL_STRING.validate_python(
                await connection.eval(
                    _REPLAY_REPLY_SCRIPT,
                    1,
                    f"mas.reply_receipt:{correlation_id}",
                    sender_id,
                    fingerprint,
                )
            )
            if result is not None:
                await self._durability.confirm(connection)
            return result

    async def write_dlq(
        self,
        *,
        envelope_json: str,
        reason: str,
        delivery: DeliveryCommit | None = None,
    ) -> bool:
        """Write a message to the DLQ stream.

        Returns ``True`` when the stream entry is safe to ack: either the write
        succeeded, or DLQ is disabled and the delivery is intentionally dropped.
        A delivery commit atomically checks lease and pending ownership before
        inserting the replacement and deleting the original. Storage failure
        propagates for a commit; stale ownership returns ``False``.
        """
        telemetry = get_telemetry()
        with telemetry.start_span(
            "mas.server.routing.write_dlq",
            kind=SpanKind.PRODUCER,
            attributes={"mas.dlq.reason": reason},
        ):
            if not self._dlq_enabled and delivery is None:
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

            fields["envelope"] = envelope_json

            try:
                wire_fields: dict[FieldT, EncodableT] = {
                    key: value for key, value in fields.items()
                }
                async with self._redis.pipeline(transaction=False) as pipeline:
                    if delivery is None:
                        pipeline.xadd("dlq:messages", wire_fields)
                    else:
                        inflight = delivery.inflight
                        lease = delivery.lease
                        pipeline.eval(
                            _COMMIT_DELIVERY_SCRIPT,
                            3,
                            inflight.stream_name,
                            f"mas.session:{lease.agent_id}:{lease.instance_id}",
                            "dlq:messages",
                            inflight.group,
                            inflight.entry_id,
                            inflight.consumer,
                            lease.owner,
                            int(self._dlq_enabled),
                            json.dumps(
                                [value for pair in fields.items() for value in pair]
                            ),
                        )
                    replies = await self._durability.execute(pipeline)
                    if delivery is not None:
                        completed = _INTEGER.validate_python(replies[0], strict=True)
                        if completed != 1:
                            return False
                telemetry.record_dlq_write(
                    result="success" if self._dlq_enabled else "disabled"
                )
                return True
            except Exception:
                telemetry.record_dlq_write(result="failed")
                telemetry.record_redis_error(component="routing", operation="xadd_dlq")
                logger.warning(
                    "Failed to write message to DLQ stream",
                    exc_info=True,
                    extra={
                        "message_id": fields["message_id"],
                        "reason": reason,
                    },
                )
                if delivery is not None:
                    raise
                return False

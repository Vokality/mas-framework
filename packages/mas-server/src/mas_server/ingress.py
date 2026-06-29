"""Ingress services for send/request/reply flows."""

from __future__ import annotations

import json
import logging
import time
import uuid
from math import ceil

from mas_core import (
    EnvelopeMessage,
    JsonObject,
    MessageMeta,
    SpanKind,
    get_telemetry,
    validate_json_object,
)
from redis.asyncio import Redis

from .errors import FailedPreconditionError, InvalidArgumentError, PermissionDeniedError
from .policy import PolicyPipeline
from .sessions import SessionManager

logger = logging.getLogger(__name__)

_DELETE_IF_VALUE_MATCHES_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class IngressService:
    """Handle message ingress and request/reply correlation."""

    def __init__(
        self,
        *,
        sessions: SessionManager,
        policy: PolicyPipeline,
        redis: Redis[str],
    ) -> None:
        """Initialize ingress service."""
        self._sessions = sessions
        self._policy = policy
        self._redis = redis

    async def send_message(
        self,
        *,
        sender_id: str,
        sender_instance_id: str,
        target_id: str,
        message_type: str,
        data_json: str,
    ) -> str:
        """Send a one-way message through policy checks and routing."""
        telemetry = get_telemetry()
        with telemetry.start_span(
            "mas.server.ingress.send",
            kind=SpanKind.PRODUCER,
            attributes={
                "mas.sender_id": sender_id,
                "mas.target_id": target_id,
                "mas.message_type": message_type,
            },
        ):
            self._sessions.ensure_connected(sender_id, sender_instance_id)
            payload = self._parse_payload_json(data_json)
            meta = MessageMeta(sender_instance_id=sender_instance_id)
            telemetry.inject_message_meta(meta)
            message = self._build_message(
                sender_id=sender_id,
                target_id=target_id,
                message_type=message_type,
                data=payload,
                meta=meta,
            )
            await self._policy.ingest_and_route(message)
            return message.message_id

    async def request_message(
        self,
        *,
        sender_id: str,
        sender_instance_id: str,
        target_id: str,
        message_type: str,
        data_json: str,
        timeout_ms: int,
    ) -> tuple[str, str]:
        """Send a request and register correlation tracking."""
        telemetry = get_telemetry()
        with telemetry.start_span(
            "mas.server.ingress.request",
            kind=SpanKind.PRODUCER,
            attributes={
                "mas.sender_id": sender_id,
                "mas.target_id": target_id,
                "mas.message_type": message_type,
            },
        ):
            self._sessions.ensure_connected(sender_id, sender_instance_id)
            payload = self._parse_payload_json(data_json)
            correlation_id = uuid.uuid4().hex

            ttl_seconds = max(1, ceil(timeout_ms / 1000.0)) if timeout_ms > 0 else 60
            expires_at = time.time() + float(ttl_seconds)
            pending_key = f"mas.pending_request:{correlation_id}"
            await self._redis.set(
                pending_key,
                json.dumps(
                    {
                        "agent_id": sender_id,
                        "instance_id": sender_instance_id,
                        "target_id": target_id,
                        "expires_at": expires_at,
                    }
                ),
                ex=ttl_seconds,
            )

            meta = MessageMeta(
                sender_instance_id=sender_instance_id,
                correlation_id=correlation_id,
                expects_reply=True,
            )
            telemetry.inject_message_meta(meta)
            message = self._build_message(
                sender_id=sender_id,
                target_id=target_id,
                message_type=message_type,
                data=payload,
                meta=meta,
            )
            try:
                await self._policy.ingest_and_route(message)
            except Exception:
                await self._delete_pending_request(
                    pending_key, operation="delete_pending_failed_route"
                )
                raise
            return message.message_id, correlation_id

    async def reply_message(
        self,
        *,
        sender_id: str,
        sender_instance_id: str,
        correlation_id: str,
        message_type: str,
        data_json: str,
    ) -> str:
        """Send a reply to a pending request."""
        telemetry = get_telemetry()
        with telemetry.start_span(
            "mas.server.ingress.reply",
            kind=SpanKind.PRODUCER,
            attributes={
                "mas.sender_id": sender_id,
                "mas.message_type": message_type,
            },
        ):
            self._sessions.ensure_connected(sender_id, sender_instance_id)
            payload = self._parse_payload_json(data_json)

            if not correlation_id:
                raise InvalidArgumentError("missing_correlation_id")

            pending_key = f"mas.pending_request:{correlation_id}"
            origin_raw = await self._redis.get(pending_key)
            if not origin_raw:
                raise InvalidArgumentError("unknown_correlation_id")

            if isinstance(origin_raw, bytes):
                try:
                    origin_text = origin_raw.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise InvalidArgumentError("unknown_correlation_id") from exc
            else:
                origin_text = str(origin_raw)

            try:
                origin_obj = json.loads(origin_text)
            except json.JSONDecodeError as exc:
                raise InvalidArgumentError("unknown_correlation_id") from exc

            if not isinstance(origin_obj, dict):
                raise InvalidArgumentError("unknown_correlation_id")

            origin_agent_id = str(origin_obj.get("agent_id", ""))
            origin_instance_id = str(origin_obj.get("instance_id", ""))
            expected_sender_id = str(origin_obj.get("target_id", ""))
            expires_at_raw = origin_obj.get("expires_at")
            if expires_at_raw is None:
                raise InvalidArgumentError("unknown_correlation_id")
            if isinstance(expires_at_raw, (int, float)):
                expires_at = float(expires_at_raw)
            elif isinstance(expires_at_raw, str):
                try:
                    expires_at = float(expires_at_raw)
                except ValueError as exc:
                    raise InvalidArgumentError("unknown_correlation_id") from exc
            else:
                raise InvalidArgumentError("unknown_correlation_id")

            if not origin_agent_id or not origin_instance_id:
                raise InvalidArgumentError("unknown_correlation_id")
            # Fail closed when the sender binding is absent.
            if not expected_sender_id:
                raise InvalidArgumentError("unknown_correlation_id")
            if sender_id != expected_sender_id:
                raise PermissionDeniedError("reply_sender_mismatch")

            if time.time() > expires_at:
                await self._delete_pending_request(
                    pending_key, operation="delete_pending_expired"
                )
                raise FailedPreconditionError("correlation_id_expired")

            reserved = await self._delete_pending_if_unchanged(
                pending_key, expected_value=origin_text
            )
            if not reserved:
                raise InvalidArgumentError("unknown_correlation_id")

            meta = MessageMeta(
                sender_instance_id=sender_instance_id,
                correlation_id=correlation_id,
                is_reply=True,
                expects_reply=False,
                reply_to_instance_id=origin_instance_id,
            )
            telemetry.inject_message_meta(meta)
            message = self._build_message(
                sender_id=sender_id,
                target_id=origin_agent_id,
                message_type=message_type,
                data=payload,
                meta=meta,
            )
            await self._policy.ingest_and_route(message)

            return message.message_id

    @staticmethod
    def _build_message(
        *,
        sender_id: str,
        target_id: str,
        message_type: str,
        data: JsonObject,
        meta: MessageMeta,
    ) -> EnvelopeMessage:
        """Build a message envelope from parsed payload and metadata."""
        return EnvelopeMessage(
            sender_id=sender_id,
            target_id=target_id,
            message_type=message_type,
            data=data,
            meta=meta,
        )

    @staticmethod
    def _parse_payload_json(data_json: str) -> JsonObject:
        """Parse payload JSON into a dictionary."""
        try:
            obj = json.loads(data_json) if data_json else {}
        except json.JSONDecodeError as exc:
            raise InvalidArgumentError("invalid_json") from exc
        try:
            return validate_json_object(obj)
        except ValueError as exc:
            raise InvalidArgumentError("payload_must_be_object") from exc

    async def _delete_pending_request(
        self, pending_key: str, *, operation: str
    ) -> None:
        """Best-effort delete for a pending request key."""
        try:
            await self._redis.delete(pending_key)
        except Exception:
            get_telemetry().record_redis_error(component="ingress", operation=operation)
            logger.debug(
                "Failed to delete pending request",
                exc_info=True,
                extra={"pending_key": pending_key, "operation": operation},
            )

    async def _delete_pending_if_unchanged(
        self, pending_key: str, *, expected_value: str
    ) -> bool:
        """Atomically reserve a pending request by deleting its unchanged value."""
        try:
            deleted = await self._redis.eval(
                _DELETE_IF_VALUE_MATCHES_SCRIPT,
                1,
                pending_key,
                expected_value,
            )
        except Exception:
            get_telemetry().record_redis_error(
                component="ingress", operation="delete_pending_if_unchanged"
            )
            logger.debug(
                "Failed to reserve pending request",
                exc_info=True,
                extra={"pending_key": pending_key},
            )
            return False
        return int(deleted) == 1

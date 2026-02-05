"""Ingress services for send/request/reply flows."""

from __future__ import annotations

import json
import logging
import time
import uuid
from math import ceil
from typing import Any

from ..protocol import EnvelopeMessage, MessageMeta
from ..redis_types import AsyncRedisProtocol
from .errors import FailedPreconditionError, InvalidArgumentError
from .policy import PolicyPipeline
from .sessions import SessionManager

logger = logging.getLogger(__name__)


class IngressService:
    """Handle message ingress and request/reply correlation."""

    def __init__(
        self,
        *,
        sessions: SessionManager,
        policy: PolicyPipeline,
        redis: AsyncRedisProtocol,
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
        self._sessions.ensure_connected(sender_id, sender_instance_id)
        payload = self._parse_payload_json(data_json)
        message = self._build_message(
            sender_id=sender_id,
            target_id=target_id,
            message_type=message_type,
            data=payload,
            meta=MessageMeta(sender_instance_id=sender_instance_id),
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
        self._sessions.ensure_connected(sender_id, sender_instance_id)
        payload = self._parse_payload_json(data_json)
        correlation_id = uuid.uuid4().hex

        ttl_seconds = max(1, int(ceil(timeout_ms / 1000.0))) if timeout_ms > 0 else 60
        expires_at = time.time() + float(ttl_seconds)
        await self._redis.setex(
            f"mas.pending_request:{correlation_id}",
            ttl_seconds,
            json.dumps(
                {
                    "agent_id": sender_id,
                    "instance_id": sender_instance_id,
                    "expires_at": expires_at,
                }
            ),
        )

        message = self._build_message(
            sender_id=sender_id,
            target_id=target_id,
            message_type=message_type,
            data=payload,
            meta=MessageMeta(
                sender_instance_id=sender_instance_id,
                correlation_id=correlation_id,
                expects_reply=True,
            ),
        )
        await self._policy.ingest_and_route(message)
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

        if time.time() > expires_at:
            try:
                await self._redis.delete(pending_key)
            except Exception:
                logger.debug(
                    "Failed to delete expired pending request",
                    exc_info=True,
                    extra={
                        "correlation_id": correlation_id,
                        "pending_key": pending_key,
                    },
                )
            raise FailedPreconditionError("correlation_id_expired")

        message = self._build_message(
            sender_id=sender_id,
            target_id=origin_agent_id,
            message_type=message_type,
            data=payload,
            meta=MessageMeta(
                sender_instance_id=sender_instance_id,
                correlation_id=correlation_id,
                is_reply=True,
                expects_reply=False,
                reply_to_instance_id=origin_instance_id,
            ),
        )
        await self._policy.ingest_and_route(message)

        try:
            await self._redis.delete(pending_key)
        except Exception:
            logger.debug(
                "Failed to delete fulfilled pending request",
                exc_info=True,
                extra={
                    "correlation_id": correlation_id,
                    "pending_key": pending_key,
                },
            )

        return message.message_id

    @staticmethod
    def _build_message(
        *,
        sender_id: str,
        target_id: str,
        message_type: str,
        data: dict[str, Any],
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
    def _parse_payload_json(data_json: str) -> dict[str, Any]:
        """Parse payload JSON into a dictionary."""
        try:
            obj = json.loads(data_json) if data_json else {}
        except json.JSONDecodeError as exc:
            raise InvalidArgumentError("invalid_json") from exc
        if not isinstance(obj, dict):
            raise InvalidArgumentError("payload_must_be_object")
        return obj

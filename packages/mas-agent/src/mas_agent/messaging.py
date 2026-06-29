"""Outbound messaging RPCs: send, request, reply, and discover."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Mapping

from mas_core import (
    JsonObject,
    JsonValue,
    SpanKind,
    get_telemetry,
    validate_json_object,
)
from mas_proto.runtime.v1 import (
    runtime_pb2 as mas_pb2,
)

from ._core import AgentCore, AgentMessage, PendingRequest
from .config import DEFAULT_REQUEST_TIMEOUT


class MessagingMixin(AgentCore):
    """Client-initiated RPCs for :class:`Agent`."""

    async def send(
        self, target_id: str, message_type: str, data: Mapping[str, JsonValue]
    ) -> None:
        """Send a one-way message to another agent."""
        telemetry = get_telemetry()
        with telemetry.start_span(
            "mas.agent.send",
            kind=SpanKind.CLIENT,
            attributes={
                "mas.agent_id": self.id,
                "mas.target_id": target_id,
                "mas.message_type": message_type,
            },
        ):
            stub = self._require_stub()
            await stub.Send(
                mas_pb2.SendRequest(
                    target_id=target_id,
                    message_type=message_type,
                    data_json=json.dumps(dict(data)),
                    instance_id=self.instance_id,
                ),
                metadata=telemetry.grpc_metadata(),
            )

    async def request(
        self,
        target_id: str,
        message_type: str,
        data: Mapping[str, JsonValue],
        timeout: float | None = None,
    ) -> AgentMessage:
        """Send a request and await a reply."""
        telemetry = get_telemetry()
        with telemetry.start_span(
            "mas.agent.request",
            kind=SpanKind.CLIENT,
            attributes={
                "mas.agent_id": self.id,
                "mas.target_id": target_id,
                "mas.message_type": message_type,
            },
        ):
            stub = self._require_stub()

            effective_timeout = DEFAULT_REQUEST_TIMEOUT if timeout is None else timeout
            if effective_timeout <= 0:
                raise ValueError("request timeout must be greater than 0")
            timeout_ms = math.ceil(effective_timeout * 1000)
            resp = await stub.Request(
                mas_pb2.RequestRequest(
                    target_id=target_id,
                    message_type=message_type,
                    data_json=json.dumps(dict(data)),
                    timeout_ms=timeout_ms,
                    instance_id=self.instance_id,
                ),
                metadata=telemetry.grpc_metadata(),
            )

            loop = asyncio.get_running_loop()
            fut: asyncio.Future[AgentMessage] = loop.create_future()
            self._pending_requests[resp.correlation_id] = PendingRequest(
                future=fut, target_id=target_id
            )

            # A reply may have raced ahead of this registration. Claim it
            # regardless of buffer age (expire=False) — this request owns the
            # correlation id, so even a reply buffered during a slow round-trip
            # is still the right one — but only if it came from the agent we sent
            # the request to (see _reply_authorized; a spoof is dropped, leaving
            # us to await the legitimate reply or time out).
            early = self._early_replies.pop(resp.correlation_id, None, expire=False)
            if (
                early is not None
                and not fut.done()
                and self._reply_authorized(early, target_id, source="early_reply")
            ):
                fut.set_result(early)

            try:
                return await asyncio.wait_for(fut, effective_timeout)
            finally:
                # Always clear this request's correlation slot. The future may be
                # done (early reply) or pending (timeout/cancel), and keeping it
                # around leaks memory over long runtimes.
                pending = self._pending_requests.get(resp.correlation_id)
                if pending is not None and pending.future is fut:
                    self._pending_requests.pop(resp.correlation_id, None)

    async def send_reply_envelope(
        self, original: AgentMessage, message_type: str, payload: JsonObject
    ) -> None:
        """Send a reply for a previously received message."""
        if not original.meta.correlation_id:
            raise RuntimeError("Cannot reply: missing correlation_id")
        telemetry = get_telemetry()
        with telemetry.start_span(
            "mas.agent.reply",
            kind=SpanKind.CLIENT,
            attributes={
                "mas.agent_id": self.id,
                "mas.target_id": original.sender_id,
                "mas.message_type": message_type,
            },
        ):
            stub = self._require_stub()
            await stub.Reply(
                mas_pb2.ReplyRequest(
                    correlation_id=original.meta.correlation_id,
                    message_type=message_type,
                    data_json=json.dumps(dict(payload)),
                    instance_id=self.instance_id,
                ),
                metadata=telemetry.grpc_metadata(),
            )

    async def discover(self, capabilities: list[str] | None = None) -> list[JsonObject]:
        """Return matching agents with optional capability filters."""
        telemetry = get_telemetry()
        with telemetry.start_span(
            "mas.agent.discover",
            kind=SpanKind.CLIENT,
            attributes={"mas.agent_id": self.id},
        ):
            stub = self._require_stub()
            resp = await stub.Discover(
                mas_pb2.DiscoverRequest(capabilities=list(capabilities or [])),
                metadata=telemetry.grpc_metadata(),
            )
            records: list[JsonObject] = []
            for rec in resp.agents:
                metadata = (
                    validate_json_object(json.loads(rec.metadata_json))
                    if rec.metadata_json
                    else {}
                )
                records.append(
                    {
                        "id": rec.agent_id,
                        "capabilities": list(rec.capabilities),
                        "metadata": metadata,
                        "status": rec.status,
                    }
                )
            return records

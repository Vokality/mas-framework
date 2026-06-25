"""Core types for strongly typed agent messaging."""

from __future__ import annotations

import time

from pydantic import BaseModel, Field, JsonValue, TypeAdapter

# External domains should define their own enums/constants; we use a string alias.
type MessageType = str
type JsonObject = dict[str, JsonValue]

_JSON_OBJECT_ADAPTER = TypeAdapter(JsonObject)
_JSON_VALUE_ADAPTER = TypeAdapter(JsonValue)


def validate_json_value(value: object) -> JsonValue:
    """Validate a raw value using Pydantic's JSON contract."""
    return _JSON_VALUE_ADAPTER.validate_python(value)


def validate_json_object(value: object) -> JsonObject:
    """Validate a raw value using Pydantic's JSON object contract."""
    return _JSON_OBJECT_ADAPTER.validate_python(value)


class MessageMeta(BaseModel):
    """
    Transport metadata not belonging to business payloads.
    """

    version: int = 1
    correlation_id: str | None = None
    expects_reply: bool = False
    is_reply: bool = False
    # Instance ID of the sending process (used for auditing/authn and debugging).
    sender_instance_id: str | None = None
    # Instance ID to route replies to (set on reply messages).
    reply_to_instance_id: str | None = None
    # W3C Trace Context fields for distributed tracing across hops.
    traceparent: str | None = None
    tracestate: str | None = None


class EnvelopeMessage(BaseModel):
    """
    Versioned message envelope used across the framework.

    - message_type: string identifier for routing/dispatch
    - data: business payload (validated by handler-registered models)
    - meta: transport metadata (correlation, reply flags, version)
    """

    sender_id: str
    target_id: str
    message_type: MessageType
    data: JsonObject
    meta: MessageMeta = Field(default_factory=MessageMeta)
    timestamp: float = Field(default_factory=time.time)
    message_id: str = Field(default_factory=lambda: str(time.time_ns()))

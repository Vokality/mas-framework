"""Minimal message protocol."""

from enum import Enum
from pydantic import BaseModel, Field
import time
from typing import Any


class MessageType(str, Enum):
    """Message types."""

    AGENT_MESSAGE = "agent_message"
    SYSTEM = "system"


class Message(BaseModel):
    """Universal message format."""

    sender_id: str
    target_id: str
    message_type: MessageType = MessageType.AGENT_MESSAGE
    payload: dict[str, Any]
    timestamp: float = Field(default_factory=time.time)
    message_id: str = Field(default_factory=lambda: str(time.time_ns()))

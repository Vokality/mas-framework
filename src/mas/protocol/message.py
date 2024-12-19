from datetime import UTC, datetime
from typing import Any, Dict
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from .types import MessageStatus, MessageType


def now() -> datetime:
    """Get the current UTC datetime."""
    return datetime.now(UTC)


class Message(BaseModel):
    """Base message type."""

    id: UUID = Field(default_factory=uuid4)
    status: MessageStatus = Field(default=MessageStatus.PENDING)
    timestamp: datetime = Field(default_factory=now)
    sender_id: str
    target_id: str
    message_type: MessageType
    payload: Dict[str, Any]

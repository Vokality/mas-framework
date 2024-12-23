from datetime import datetime
from typing import Any, Dict
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from ._util import empty_dict, utc_now
from .types import MessageStatus, MessageType


class Message(BaseModel):
    """Base message type."""

    id: UUID = Field(default_factory=uuid4)
    status: MessageStatus = Field(default=MessageStatus.PENDING)
    timestamp: datetime = Field(default_factory=utc_now)
    sender_id: str
    target_id: str
    message_type: MessageType
    payload: Dict[str, Any] = Field(default_factory=empty_dict)

"""
Protocol types for strongly-typed agent messaging.
"""

from __future__ import annotations


import time
from typing import Any, Optional, TypeAlias, TYPE_CHECKING

from pydantic import BaseModel, Field, PrivateAttr

if TYPE_CHECKING:
    from .agent import Agent  # forward reference for private attachment

# External domains should define their own enums/constants; we use a string alias.
MessageType: TypeAlias = str


class MessageMeta(BaseModel):
    """
    Transport metadata not belonging to business payloads.
    """

    version: int = 1
    correlation_id: Optional[str] = None
    expects_reply: bool = False
    is_reply: bool = False


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
    data: dict[str, Any]
    meta: MessageMeta = Field(default_factory=MessageMeta)
    timestamp: float = Field(default_factory=time.time)
    message_id: str = Field(default_factory=lambda: str(time.time_ns()))

    # Allow attaching agent reference for reply() convenience
    _agent: Optional["Agent[Any]"] = PrivateAttr(default=None)

    model_config = {"arbitrary_types_allowed": True}

    # Back-compat convenience for gateway modules that expect 'payload'
    @property
    def payload(self) -> dict[str, Any]:
        return self.data

    @payload.setter
    def payload(self, value: dict[str, Any]) -> None:
        self.data = value

    # Backward compatibility properties
    @property
    def expects_reply(self) -> bool:
        """Check if this message expects a reply."""
        return self.meta.expects_reply

    @property
    def is_reply(self) -> bool:
        """Check if this message is a reply to a previous request."""
        return self.meta.is_reply

    def attach_agent(self, agent: "Agent[Any]") -> None:
        self._agent = agent

    async def reply(self, message_type: MessageType, payload: dict[str, Any]) -> None:
        """
        Reply to this message using correlation metadata.

        Args:
            message_type: Message type for the reply
            payload: Response payload dictionary

        Raises:
            RuntimeError: If agent is not available or message doesn't expect reply
        """
        if not self._agent:
            raise RuntimeError(
                "Cannot reply: message not associated with agent. "
                "This should not happen in normal operation."
            )

        if not self.meta.correlation_id:
            raise RuntimeError(
                "Cannot reply: message does not have correlation ID. "
                "Only messages sent via request() can be replied to."
            )

        await self._agent.send_reply_envelope(self, message_type, payload)

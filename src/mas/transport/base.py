from abc import ABC, abstractmethod
from typing import AsyncGenerator

from mas.protocol import Message


class BaseTransport(ABC):
    """Transport layer interface."""

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize transport."""
        pass

    @abstractmethod
    async def publish(self, message: Message) -> None:
        """Publish a message."""
        pass

    @abstractmethod
    async def subscribe(self, channel: str) -> None:
        """Subscribe to messages on a channel.

        Args:
            channel: Channel identifier to subscribe to

        Returns:
            AsyncGenerator yielding messages from the channel
        """
        pass

    @abstractmethod
    async def get_message_stream(self, channel: str) -> AsyncGenerator[Message, None]:
        """Get message stream for a channel."""
        pass

    @abstractmethod
    async def cleanup(self) -> None:
        """Cleanup transport resources."""
        pass

    @abstractmethod
    async def unsubscribe(self, channel: str) -> None:
        """Unsubscribe from a channel.

        Args:
            channel: Channel to unsubscribe from
        """
        pass

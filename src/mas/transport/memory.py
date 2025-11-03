import asyncio
from typing import AsyncGenerator, Dict, Set

from mas.logger import get_logger
from mas.protocol import Message
from mas.transport.base import BaseTransport

logger = get_logger()


class MemoryTransport(BaseTransport):
    """In-memory transport for testing."""

    def __init__(self):
        self._subscriptions: Dict[str, Set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialize the transport."""
        logger.debug("MemoryTransport initialized")

    async def publish(self, message: Message) -> None:
        """Publish a message to subscribers."""
        async with self._lock:
            if message.target_id in self._subscriptions:
                for queue in self._subscriptions[message.target_id]:
                    try:
                        await queue.put(message)
                    except Exception as e:
                        logger.error(f"Failed to deliver message: {e}")

    async def subscribe(self, channel: str) -> None:
        """Subscribe to a channel."""
        async with self._lock:
            if channel not in self._subscriptions:
                self._subscriptions[channel] = set()

    async def unsubscribe(self, channel: str) -> None:
        """Unsubscribe from a channel."""
        async with self._lock:
            if channel in self._subscriptions:
                # Signal end of stream to all subscribers
                for queue in self._subscriptions[channel]:
                    try:
                        await queue.put(None)
                    except Exception:
                        pass
                del self._subscriptions[channel]

    async def get_message_stream(self, channel: str) -> AsyncGenerator[Message, None]:
        """Get message stream for a channel."""
        async with self._lock:
            if channel not in self._subscriptions:
                raise RuntimeError(f"Not subscribed to channel: {channel}")
            queue = asyncio.Queue()
            self._subscriptions[channel].add(queue)

        try:
            while True:
                message = await queue.get()
                if message is None:  # End of stream
                    break
                yield message
        finally:
            async with self._lock:
                if channel in self._subscriptions:
                    self._subscriptions[channel].discard(queue)

    async def cleanup(self) -> None:
        """Cleanup transport resources."""
        async with self._lock:
            for queues in self._subscriptions.values():
                for queue in queues:
                    try:
                        await queue.put(None)
                    except Exception:
                        pass
            self._subscriptions.clear()
        logger.debug("MemoryTransport cleaned up")
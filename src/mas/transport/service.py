import asyncio
from contextlib import asynccontextmanager
from enum import Enum, auto
from typing import Dict, Set

from mas.logger import get_logger
from mas.protocol import Message

from .redis import RedisTransport

logger = get_logger()


class ServiceState(Enum):
    """Transport service states."""

    INITIALIZED = auto()
    RUNNING = auto()
    SHUTTING_DOWN = auto()
    SHUTDOWN = auto()


class TransportService:
    """
    Transport service facade that manages message routing and subscription state.
    Does not directly manage transport connections.
    """

    def __init__(self, transport: RedisTransport):
        logger.info("Initializing TransportService")
        self.transport = transport
        self._state = ServiceState.INITIALIZED
        self._state_lock = asyncio.Lock()
        self._active_components: Set[str] = set()
        self._shutdown_complete = asyncio.Event()
        self._subscriptions: Dict[str, Set[str]] = {}
        self._subscription_lock = asyncio.Lock()
        logger.debug("TransportService initialized")

    @property
    def state(self) -> ServiceState:
        """Current service state."""
        return self._state

    async def _set_state(self, new_state: ServiceState) -> None:
        """Thread-safe state transition."""
        async with self._state_lock:
            self._state = new_state
            logger.debug(f"Transport service state changed to {new_state.name}")

    async def subscribe(self, subscriber_id: str, channel: str) -> None:
        """
        Register a subscription for a subscriber.

        Args:
            subscriber_id: ID of the subscribing entity
            channel: Channel to subscribe to
        """
        logger.debug(f"Attempting to subscribe {subscriber_id} to channel {channel}")
        if self._state != ServiceState.RUNNING:
            logger.error("Cannot subscribe: Transport service not running")
            raise RuntimeError("Transport service not running")

        async with self._subscription_lock:
            logger.debug(f"Acquired subscription lock for {subscriber_id}")
            if subscriber_id not in self._subscriptions:
                logger.debug(f"Creating new subscription set for {subscriber_id}")
                self._subscriptions[subscriber_id] = set()

            if channel in self._subscriptions[subscriber_id]:
                logger.error(f"{subscriber_id} already subscribed to {channel}")
                raise RuntimeError(f"Already subscribed to channel: {channel}")

            try:
                logger.debug(f"Subscribing {subscriber_id} to {channel} via transport")
                await self.transport.subscribe(channel)
                self._subscriptions[subscriber_id].add(channel)
                logger.info(f"Successfully subscribed {subscriber_id} to {channel}")
            except Exception as e:
                logger.error(f"Failed to subscribe {subscriber_id} to {channel}: {e}")
                raise

    async def unsubscribe(self, subscriber_id: str, channel: str) -> None:
        """
        Remove a subscription for a subscriber.

        Args:
            subscriber_id: ID of the subscribing entity
            channel: Channel to unsubscribe from
        """
        logger.debug(f"Attempting to unsubscribe {subscriber_id} from {channel}")
        async with self._subscription_lock:
            if subscriber_id in self._subscriptions:
                if channel in self._subscriptions[subscriber_id]:
                    try:
                        logger.debug(f"Unsubscribing {subscriber_id} from {channel}")
                        await self.transport.unsubscribe(channel)
                        self._subscriptions[subscriber_id].remove(channel)
                        logger.info(
                            f"Successfully unsubscribed {subscriber_id} from {channel}"
                        )

                        # Clean up subscriber entry if no more subscriptions
                        if not self._subscriptions[subscriber_id]:
                            logger.debug(
                                f"Removing empty subscription set for {subscriber_id}"
                            )
                            del self._subscriptions[subscriber_id]
                    except Exception as e:
                        logger.error(
                            f"Failed to unsubscribe {subscriber_id} from {channel}: {e}"
                        )
                        raise

    async def send_message(self, message: Message) -> None:
        """
        Send a message through the transport layer.

        Args:
            message: Message to send
        """
        logger.debug(
            f"Attempting to send message from {message.sender_id} to {message.target_id}"
        )
        if self._state != ServiceState.RUNNING:
            logger.error("Cannot send message: Transport service not running")
        try:
            await self.transport.publish(message)
            logger.debug(f"Successfully sent message {message.id}")
        except Exception as e:
            logger.error(f"Failed to send message {message.id}: {e}")
            raise

    @asynccontextmanager
    async def message_stream(self, subscriber_id: str, channel: str):
        """
        Context manager for receiving messages on a channel.

        Args:
            subscriber_id: ID of the subscribing entity
            channel: Channel to receive messages from
        """
        logger.debug(f"Setting up message stream for {subscriber_id} on {channel}")
        try:
            # First subscribe to the channel
            logger.debug(f"Subscribing {subscriber_id} to {channel}")
            await self.subscribe(subscriber_id, channel)
            # Then get the message stream
            logger.debug(f"Getting message stream for {channel}")
            message_stream = self.transport.get_message_stream(channel)
            logger.info(f"Message stream established for {subscriber_id} on {channel}")
            yield message_stream
        except Exception as e:
            logger.error(f"Error in message stream setup: {e}")
            raise
        finally:
            logger.debug(f"Cleaning up message stream for {subscriber_id} on {channel}")
            await self.unsubscribe(subscriber_id, channel)

    async def start(self) -> None:
        """Start the transport service."""
        logger.info("Starting transport service")
        if self._state != ServiceState.INITIALIZED:
            logger.debug("Transport service already started")
            return

        try:
            await self.transport.initialize()
            await self._set_state(ServiceState.RUNNING)
            logger.info("Transport service started successfully")
        except Exception as e:
            logger.error(f"Failed to start transport service: {e}")
            raise

    async def register_component(self, component: str) -> None:
        """Register an agent with the transport service."""
        logger.info(f"Registering {component} with transport service")
        async with self._state_lock:
            if self._state != ServiceState.RUNNING:
                logger.error(f"Cannot register {component}: Service not running")
                raise RuntimeError("Transport service not running")
            self._active_components.add(component)
            logger.info(f"{component} registered successfully")

    async def deregister_component(self, component: str) -> None:
        """Deregister an agent from the transport service."""
        logger.info(f"Deregistering {component} from transport service")
        async with self._state_lock:
            self._active_components.discard(component)
            # Clean up agent's subscriptions
            if component in self._subscriptions:
                channels = list(self._subscriptions[component])
                logger.debug(
                    f"Cleaning up {len(channels)} subscriptions for agent {component}"
                )
                for channel in channels:
                    await self.unsubscribe(component, channel)
            if not self._active_components:
                logger.info("All agents deregistered, signaling shutdown complete")
                self._shutdown_complete.set()

    async def stop(self) -> None:
        """
        Stop the transport service.
        Handles both legacy and new agent coordination modes.
        """
        if self._state != ServiceState.RUNNING:
            logger.debug("Transport service not running, skipping stop")
            return

        logger.info("Initiating transport service shutdown")
        await self._set_state(ServiceState.SHUTTING_DOWN)

        # Clean up all subscriptions first
        async with self._subscription_lock:
            subscribers = list(self._subscriptions.keys())
            logger.debug(
                f"Cleaning up subscriptions for {len(subscribers)} subscribers"
            )
            for subscriber_id in subscribers:
                channels = list(self._subscriptions[subscriber_id])
                for channel in channels:
                    try:
                        logger.debug(f"Unsubscribing {subscriber_id} from {channel}")
                        await self.unsubscribe(subscriber_id, channel)
                    except Exception as e:
                        logger.error(
                            f"Error unsubscribing {subscriber_id} from {channel}: {e}"
                        )

        if self._active_components:
            logger.info(
                f"Waiting for {len(self._active_components)} agents to deregister"
            )
            try:
                await asyncio.wait_for(self._shutdown_complete.wait(), timeout=10.0)
                logger.debug("All components successfully deregistered")
            except asyncio.TimeoutError:
                logger.warning("Timeout waiting for agents to deregister")
                # Force cleanup of remaining agents
                remaining = list(self._active_components)
                logger.debug(f"Force cleaning up {len(remaining)} remaining agents")
                for agent_id in remaining:
                    await self.deregister_component(agent_id)

        # Clean up transport
        logger.debug("Cleaning up transport")
        await self.transport.cleanup()
        await self._set_state(ServiceState.SHUTDOWN)
        logger.info("Transport service shutdown complete")

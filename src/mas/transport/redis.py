import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import (
    AsyncGenerator,
    AsyncIterator,
    Dict,
    Optional,
    Tuple,
    override,
    Coroutine,
    Any,
)
from uuid import uuid4

from redis.asyncio.client import PubSub
from redis.asyncio.connection import ConnectionPool
from redis.exceptions import ConnectionError, RedisError

from mas.logger import get_logger
from mas.protocol import Message
from mas.transport.interfaces import BaseTransport
from mas.transport.metrics import TransportMetrics
from mas.transport.task import TaskManager

from .connection import RedisConnectionManager

logger = get_logger()


@dataclass
class SubscriptionState:
    """State for a channel subscription."""

    channel: str
    subscriber_count: int = 0
    pubsub: Optional[PubSub] = None
    task: Optional[asyncio.Task] = None
    message_queues: Dict[int, asyncio.Queue] = field(default_factory=dict)
    next_subscriber_id: int = 0

    async def add_subscriber(self) -> Tuple[int, asyncio.Queue]:
        """Add a new subscriber and return its ID and queue."""
        subscriber_id = self.next_subscriber_id
        self.next_subscriber_id += 1
        self.message_queues[subscriber_id] = asyncio.Queue()
        if self.subscriber_count >= 0:  # Prevent negative counts
            self.subscriber_count += 1
        else:
            self.subscriber_count = 1
        return subscriber_id, self.message_queues[subscriber_id]

    async def remove_subscriber(self, subscriber_id: int) -> None:
        """Remove a subscriber and its queue."""
        if subscriber_id in self.message_queues:
            try:
                await self.message_queues[subscriber_id].put(
                    None
                )  # Signal end of stream
            except Exception:
                pass  # Queue might be closed
            del self.message_queues[subscriber_id]
            if self.subscriber_count > 0:  # Prevent negative counts
                self.subscriber_count -= 1


@dataclass
class DeliveryState:
    """Tracks message delivery state."""

    message_id: str
    delivery_id: str
    sender_id: str
    target_id: str
    timestamp: datetime
    retries: int = 0
    status: str = "pending"
    max_retries: int = 3


class RedisTransport(BaseTransport):
    """Redis-based transport implementation with enhanced reliability."""

    def __init__(
        self,
        url: str = "redis://localhost",
        pool: Optional[ConnectionPool] = None,
    ) -> None:
        """
        Initialize transport with optional connection pool.

        Args:
            url: Redis URL (used only if pool not provided)
            pool: Optional pre-configured connection pool
        """
        logger.info(f"Initializing RedisTransport with url={url}")
        self.connection_manager = RedisConnectionManager(
            url=url,
            pool=pool,
            pool_size=50,
        )
        self._task_manager = TaskManager()
        self._subscriptions: Dict[str, SubscriptionState] = {}
        self._deliveries: Dict[str, DeliveryState] = {}
        self._shutdown_event = asyncio.Event()
        self._subscription_lock = asyncio.Lock()
        self._delivery_lock = asyncio.Lock()
        self._shutdown_lock = asyncio.Lock()
        self.metrics = TransportMetrics()
        self._loop = asyncio.get_running_loop()

    @override
    async def initialize(self) -> None:
        """Initialize the transport with delivery tracking."""
        logger.info("Starting transport initialization")
        self._shutdown_event.clear()  # Reset shutdown flag
        await self.connection_manager.initialize()
        await self.metrics.initialize()

        # Start background tasks with proper tracking
        await self._task_manager.create_task(
            "delivery_cleanup",
            self._cleanup_old_deliveries,
            timeout=300,  # 5-minute max for cleanup cycle
        )
        logger.info("Transport initialization completed")

    async def validate_message(self, message: Message) -> bool:
        """
        Validate a message before publishing.

        Validation rules:
        1. Message must have a valid sender and target
        2. Message mustn't be expired
        3. Message mustn't be a duplicate
        """
        if not message.target_id:
            raise ValueError("Message must have target_id")

        if not message.sender_id:
            raise ValueError("Message must have sender_id")

        # Validate message hasn't expired
        message_age = datetime.now(UTC) - message.timestamp
        if message_age > timedelta(minutes=5):
            raise ValueError("Message has expired")

        # Check for duplicates in recent deliveries
        async with self._delivery_lock:
            for delivery in self._deliveries.values():
                if (
                    delivery.message_id == str(message.id)
                    and delivery.sender_id == message.sender_id
                    and delivery.target_id == message.target_id
                    and datetime.now(UTC) - delivery.timestamp < timedelta(minutes=5)
                ):
                    await self.metrics.record_duplicate_attempt(
                        str(message.id), message.sender_id, message.target_id
                    )
                    raise ValueError("Duplicate message detected")

        return True

    @override
    async def publish(self, message: Message) -> None:
        """Publish a message to Redis with delivery tracking."""
        if self._shutdown_event.is_set():
            raise RuntimeError("Transport is shutting down")

        start_time = datetime.now(UTC)
        delivery_id = str(uuid4())

        try:
            # Validate message
            await self.validate_message(message)

            # Create delivery tracking
            delivery_state = DeliveryState(
                message_id=str(message.id),
                delivery_id=delivery_id,
                sender_id=message.sender_id,
                target_id=message.target_id,
                timestamp=datetime.now(UTC),
            )

            # Record message attempt
            await self.metrics.record_message_sent(
                str(message.id),
                message.sender_id,
                message.target_id,
                delivery_id,
            )

            try:
                async with self.connection_manager.get_connection() as redis:
                    # Atomic publish with delivery tracking
                    async with redis.pipeline() as pipe:
                        # Store delivery state
                        delivery_key = f"delivery:{delivery_id}"
                        await pipe.setex(
                            delivery_key,
                            300,
                            "pending",
                        )

                        # Publish message
                        message_data = message.model_dump_json()
                        await pipe.publish(message.target_id, message_data)

                        # Execute atomic operation
                        await pipe.execute()

                        # Track delivery locally
                        async with self._delivery_lock:
                            self._deliveries[delivery_id] = delivery_state

                        # Record successful delivery and latency
                        delivery_time = datetime.now(UTC)
                        latency = (delivery_time - start_time).total_seconds() * 1000
                        await self.metrics.record_message_delivered(
                            str(message.id),
                            latency,
                        )

                        logger.debug(
                            f"Published message {message.id} to {message.target_id} "
                            f"(delivery_id: {delivery_id}, latency: {latency:.2f}ms)"
                        )

            except RedisError as e:
                logger.error(f"Failed to publish message {message.id}: {e}")
                # Update delivery state on failure
                if delivery_id in self._deliveries:
                    self._deliveries[delivery_id].status = "failed"
                await self.metrics.record_delivery_failed(str(message.id), str(e))

        except Exception as e:
            # Ensure any error is properly propagated
            logger.error(f"Error publishing message: {e}")
            raise

    async def _cleanup_old_deliveries(self) -> None:
        """Periodically clean up old delivery records."""
        while not self._shutdown_event.is_set():
            try:
                async with self._delivery_lock:
                    current_time = datetime.now(UTC)
                    expired_deliveries = [
                        delivery_id
                        for delivery_id, state in self._deliveries.items()
                        if current_time - state.timestamp > timedelta(minutes=5)
                    ]

                    for delivery_id in expired_deliveries:
                        del self._deliveries[delivery_id]

                    if expired_deliveries:
                        logger.debug(
                            f"Cleaned up {len(expired_deliveries)} old deliveries"
                        )

            except Exception as e:
                logger.error(f"Error in delivery cleanup: {e}")
            await asyncio.sleep(60)  # Run cleanup every minute

    async def _message_listener(self, state: SubscriptionState) -> None:
        """Enhanced message listener with delivery acknowledgment."""
        try:
            if not state.pubsub:
                return

            async for message in state.pubsub.listen():
                if self._shutdown_event.is_set():
                    break

                if message.get("type") == "message":
                    try:
                        parsed_message = Message.model_validate_json(
                            message.get("data")
                        )
                        # Validate target matches subscription
                        if parsed_message.target_id != state.channel:
                            logger.warning(
                                f"Received message with mismatched target_id: "
                                f"{parsed_message.target_id} on channel {state.channel}"
                            )
                            continue

                        # Deliver to each subscriber's queue
                        for queue in state.message_queues.values():
                            await queue.put(parsed_message)

                        logger.debug(
                            f"Delivered message {parsed_message.id} to "
                            f"{state.subscriber_count} subscribers on {state.channel}"
                        )

                    except Exception as e:
                        logger.error(f"Failed to parse message: {e}")
                        await self.metrics.record_subscription_error()
                        continue

        except asyncio.CancelledError:
            logger.debug(f"Message listener cancelled for {state.channel}")
        except ConnectionError as e:
            if not self._shutdown_event.is_set():
                logger.error(f"Connection lost for {state.channel}: {e}")
                await self.metrics.record_connection_error()
        except Exception as e:
            if not self._shutdown_event.is_set():
                logger.error(f"Error in message listener for {state.channel}: {e}")
                await self.metrics.record_subscription_error()
        finally:
            # Signal end of stream to all subscribers
            if not self._shutdown_event.is_set():
                for queue in state.message_queues.values():
                    try:
                        await queue.put(None)
                    except Exception as e:
                        logger.error(f"Error signaling end of stream: {e}")

    @asynccontextmanager
    async def _get_subscription(
        self, channel: str
    ) -> AsyncIterator[Tuple[SubscriptionState, asyncio.Queue]]:
        """Get or create a subscription state and return subscriber queue."""
        subscriber_id = None
        queue = None
        state = None

        try:
            async with self._subscription_lock:
                state = self._subscriptions.get(channel)
                if not state:
                    state = SubscriptionState(channel=channel)
                    self._subscriptions[channel] = state
                    state.pubsub = await self.connection_manager.get_pubsub(channel)
                    state.task = self._loop.create_task(
                        self._message_listener(state),
                        name=f"message_listener_{channel}",
                    )

                subscriber_id, queue = await state.add_subscriber()
                logger.debug(
                    f"Added subscriber {subscriber_id} to {channel}: "
                    f"count={state.subscriber_count}"
                )

            try:
                yield state, queue
            except asyncio.CancelledError:
                logger.debug(f"Subscription cancelled for {channel}")
                raise

        finally:
            if state and subscriber_id is not None:
                try:
                    async with self._subscription_lock:
                        await state.remove_subscriber(subscriber_id)
                        logger.debug(
                            f"Removed subscriber {subscriber_id} from {channel}: "
                            f"count={state.subscriber_count}"
                        )
                        if state.subscriber_count <= 0:
                            await self._cleanup_subscription(channel)
                except Exception as e:
                    logger.error(f"Error during subscription cleanup: {e}")

    async def _cleanup_subscription(self, channel: str) -> None:
        """Clean up a subscription and its resources."""
        logger.info(f"Cleaning up subscription for channel {channel}")
        try:
            if state := self._subscriptions.get(channel):
                # Clean up queues first
                for subscriber_id in list(state.message_queues.keys()):
                    try:
                        await state.remove_subscriber(subscriber_id)
                    except Exception:
                        pass

                # Cancel message listener task using current loop
                if state.task and not state.task.done():
                    try:
                        state.task.cancel()
                        try:
                            await asyncio.shield(
                                asyncio.wait_for(state.task, timeout=1.0)
                            )
                        except (asyncio.CancelledError, asyncio.TimeoutError):
                            pass
                    except Exception as e:
                        logger.error(
                            f"Error cancelling listener task for {channel}: {e}"
                        )

                # Clean up PubSub
                if state.pubsub:
                    try:
                        await state.pubsub.unsubscribe(channel)
                        await state.pubsub.aclose()
                        state.pubsub = None
                    except Exception as e:
                        logger.error(f"PubSub cleanup failed for {channel}: {e}")

                # Finally remove the subscription state
                del self._subscriptions[channel]
                logger.info(f"Successfully cleaned up subscription for {channel}")

        except Exception as e:
            logger.error(f"Error during subscription cleanup for {channel}: {e}")
            # Still try to remove subscription from tracking
            self._subscriptions.pop(channel, None)
            raise

    @override
    async def subscribe(self, channel: str) -> None:
        """Subscribe to a channel without returning a stream."""
        if self._shutdown_event.is_set():
            raise RuntimeError("Transport is shutting down")

        async with self._subscription_lock:
            state = self._subscriptions.get(channel)
            if not state:
                state = SubscriptionState(channel=channel)
                self._subscriptions[channel] = state
                state.pubsub = await self.connection_manager.get_pubsub(channel)
                state.task = self._loop.create_task(
                    self._message_listener(state),
                    name=f"message_listener_{channel}",
                )

    async def get_message_stream(self, channel: str) -> AsyncGenerator[Message, None]:
        """Get message stream for a channel."""
        if self._shutdown_event.is_set():
            raise RuntimeError("Transport is shutting down")

        async with self._subscription_lock:
            state = self._subscriptions.get(channel)
            if not state:
                raise RuntimeError(f"Not subscribed to channel: {channel}")

            subscriber_id, queue = await state.add_subscriber()

        try:
            while not self._shutdown_event.is_set():
                message = await queue.get()
                if message is None:  # End of stream
                    break
                yield message
        finally:
            if state := self._subscriptions.get(channel):
                await state.remove_subscriber(subscriber_id)

    @override
    async def unsubscribe(self, channel: str) -> None:
        """Unsubscribe from a channel and cleanup resources."""
        async with self._subscription_lock:
            if state := self._subscriptions.get(channel):
                state.subscriber_count = 0
                await self._cleanup_subscription(channel)

    @override
    async def cleanup(self) -> None:
        """Enhanced cleanup with proper task handling."""
        async with self._shutdown_lock:
            if self._shutdown_event.is_set():
                logger.info("Cleanup already in progress")
                return

            logger.info("Starting transport cleanup")
            self._shutdown_event.set()

            try:
                # Cancel background tasks
                logger.info("Cancelling background tasks")
                await self._task_manager.cancel_all()

                # Clean up subscriptions
                async with self._subscription_lock:
                    channels = list(self._subscriptions.keys())
                    if channels:
                        logger.info(f"Cleaning up {len(channels)} subscriptions")
                        for channel in channels:
                            state = self._subscriptions[channel]
                            state.subscriber_count = 0  # Force cleanup
                            try:
                                await self._cleanup_subscription(channel)
                            except Exception as e:
                                logger.error(
                                    f"Subscription cleanup failed for {channel}: {e}"
                                )

                # Clean up metrics
                logger.info("Cleaning up metrics")
                try:
                    await self.metrics.cleanup()
                except Exception as e:
                    logger.error(f"Metrics cleanup failed: {e}")

                # Clean up connection manager
                logger.info("Cleaning up connection manager")
                try:
                    await self.connection_manager.cleanup()
                except Exception as e:
                    logger.error(f"Connection manager cleanup failed: {e}")

                # Reset state
                logger.info("Resetting transport state")
                self._subscriptions.clear()
                self._deliveries.clear()

            except Exception as e:
                logger.error(f"Transport cleanup failed: {e}")
                raise
            finally:
                logger.info("Transport cleanup completed")

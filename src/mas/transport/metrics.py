"""Transport metrics collection and monitoring."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Dict, List, Optional

from mas.logger import get_logger

logger = get_logger()


@dataclass
class MessageMetrics:
    """Metrics for a single message."""

    message_id: str
    sender_id: str
    target_id: str
    delivery_id: str
    timestamp: datetime
    delivery_latency: Optional[float] = None
    status: str = "pending"
    retries: int = 0


class TransportMetrics:
    """Collects and tracks transport layer metrics."""

    def __init__(self) -> None:
        # Message counts
        self.total_messages: int = 0
        self.failed_deliveries: int = 0
        self.duplicate_attempts: int = 0

        # Subscription metrics
        self.active_subscriptions: int = 0
        self.subscription_errors: int = 0

        # Connection metrics
        self.connection_errors: int = 0
        self.reconnection_attempts: int = 0

        # Performance metrics
        self.message_latencies: List[float] = []

        # Recent message tracking
        self.recent_messages: Dict[str, MessageMetrics] = {}

        # Locks and tasks
        self._metrics_lock = asyncio.Lock()
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None
        self._loop = asyncio.get_running_loop()

    async def initialize(self) -> None:
        """Initialize metrics tracking."""
        if self._cleanup_task:
            return

        self._cleanup_task = self._loop.create_task(
            self._cleanup_old_metrics(),
            name="metrics_cleanup_task",
        )
        logger.debug("Transport metrics initialized")

    async def record_message_sent(
        self,
        message_id: str,
        sender_id: str,
        target_id: str,
        delivery_id: str,
    ) -> None:
        """Record a message being sent."""
        async with self._metrics_lock:
            self.total_messages += 1
            self.recent_messages[message_id] = MessageMetrics(
                message_id=message_id,
                sender_id=sender_id,
                target_id=target_id,
                delivery_id=delivery_id,
                timestamp=datetime.now(UTC),
            )

    async def record_message_delivered(
        self, message_id: str, delivery_latency: float
    ) -> None:
        """Record successful message delivery."""
        async with self._metrics_lock:
            if message_id in self.recent_messages:
                metrics = self.recent_messages[message_id]
                metrics.status = "delivered"
                metrics.delivery_latency = delivery_latency
                self.message_latencies.append(delivery_latency)

                # Keep only last 1000 latencies
                if len(self.message_latencies) > 1000:
                    self.message_latencies = self.message_latencies[-1000:]

    async def record_delivery_failed(self, message_id: str, error: str) -> None:
        """Record a failed message delivery."""
        async with self._metrics_lock:
            self.failed_deliveries += 1
            if message_id in self.recent_messages:
                metrics = self.recent_messages[message_id]
                metrics.status = "failed"

    async def record_duplicate_attempt(
        self, message_id: str, sender_id: str, target_id: str
    ) -> None:
        """Record a duplicate message attempt."""
        async with self._metrics_lock:
            self.duplicate_attempts += 1
            logger.warning(
                f"Duplicate message attempt: {message_id} "
                f"from {sender_id} to {target_id}"
            )

    async def record_subscription_change(self, delta: int) -> None:
        """Record subscription count change."""
        async with self._metrics_lock:
            self.active_subscriptions += delta

    async def record_subscription_error(self) -> None:
        """Record a subscription error."""
        async with self._metrics_lock:
            self.subscription_errors += 1

    async def record_connection_error(self) -> None:
        """Record a connection error."""
        async with self._metrics_lock:
            self.connection_errors += 1

    async def record_reconnection_attempt(self) -> None:
        """Record a reconnection attempt."""
        async with self._metrics_lock:
            self.reconnection_attempts += 1

    async def get_current_metrics(self) -> Dict:
        """Get current metrics snapshot."""
        async with self._metrics_lock:
            avg_latency = (
                sum(self.message_latencies) / len(self.message_latencies)
                if self.message_latencies
                else 0
            )

            return {
                "messages": {
                    "total": self.total_messages,
                    "failed": self.failed_deliveries,
                    "duplicates": self.duplicate_attempts,
                },
                "subscriptions": {
                    "active": self.active_subscriptions,
                    "errors": self.subscription_errors,
                },
                "connections": {
                    "errors": self.connection_errors,
                    "reconnections": self.reconnection_attempts,
                },
                "performance": {
                    "avg_latency_ms": avg_latency,
                    "recent_failures_rate": (
                        self.failed_deliveries / max(self.total_messages, 1)
                    ),
                },
            }

    async def _cleanup_old_metrics(self) -> None:
        """Clean up old message metrics."""
        while True:
            try:
                async with self._metrics_lock:
                    current_time = datetime.now(UTC)
                    expired_messages = [
                        msg_id
                        for msg_id, metrics in self.recent_messages.items()
                        if current_time - metrics.timestamp > timedelta(minutes=30)
                    ]

                    for msg_id in expired_messages:
                        del self.recent_messages[msg_id]

                    if expired_messages:
                        logger.debug(
                            f"Cleaned up metrics for {len(expired_messages)} old messages"
                        )

            except Exception as e:
                logger.error(f"Error in metrics cleanup: {e}")

            await asyncio.sleep(300)  # Clean up every 5 minutes

    async def cleanup(self) -> None:
        """Clean up metrics collection."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                self._loop.create_future().set_result(self._cleanup_task)
            except asyncio.CancelledError:
                pass

        async with self._metrics_lock:
            self.recent_messages.clear()
            self.message_latencies.clear()

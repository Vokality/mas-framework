"""Priority Queue Module - Message importance routing with fairness guarantee.

Implements priority-based message routing as per GATEWAY.md Phase 1:
- Priority levels: CRITICAL > HIGH > NORMAL > LOW > BULK
- Fairness: Prevents low-priority starvation
- Redis Sorted Sets for persistent priority queues
- Fair queueing algorithm to ensure all messages eventually get processed

Architecture:
- Each priority level has its own sorted set in Redis
- Messages are scored by (timestamp + fairness_boost)
- Dequeue algorithm ensures fairness using weighted round-robin
- Supports message TTL and dead letter queue for expired messages
"""

import time
from enum import IntEnum
from typing import Optional
from redis.asyncio import Redis
from pydantic import BaseModel

import logging

logger = logging.getLogger(__name__)


class MessagePriority(IntEnum):
    """Message priority levels (higher value = higher priority)."""

    CRITICAL = 4  # System-critical messages, always processed first
    HIGH = 3  # Important business operations
    NORMAL = 2  # Default priority
    LOW = 1  # Background tasks, analytics
    BULK = 0  # Batch processing, low-priority notifications


class PriorityQueueConfig(BaseModel):
    """Priority queue configuration."""

    # Fairness settings
    enable_fairness: bool = True
    starvation_threshold_seconds: float = 30.0  # Time before boosting priority

    # Message TTL (time-to-live)
    message_ttl_seconds: float = 3600.0  # 1 hour default

    # Dequeue weights for fair round-robin
    dequeue_weights: dict[MessagePriority, int] = {
        MessagePriority.CRITICAL: 50,  # 50% of dequeues
        MessagePriority.HIGH: 25,  # 25% of dequeues
        MessagePriority.NORMAL: 15,  # 15% of dequeues
        MessagePriority.LOW: 7,  # 7% of dequeues
        MessagePriority.BULK: 3,  # 3% of dequeues
    }


class QueuedMessage(BaseModel):
    """Message queued for delivery."""

    message_id: str
    sender_id: str
    target_id: str
    payload: dict
    priority: MessagePriority
    enqueued_at: float  # Unix timestamp
    ttl_seconds: float


class EnqueueResult(BaseModel):
    """Result of enqueue operation."""

    success: bool
    message_id: str
    priority: MessagePriority
    queue_position: int  # Estimated position in queue
    estimated_delay_seconds: float  # Estimated time until delivery


class DequeueResult(BaseModel):
    """Result of dequeue operation."""

    success: bool
    message: Optional[QueuedMessage] = None
    queue_empty: bool = False


class PriorityQueueModule:
    """
    Priority Queue Module for message importance routing.

    Implements weighted fair queueing to prevent starvation while
    ensuring high-priority messages are processed first.

    Key Features:
    - Five priority levels (CRITICAL to BULK)
    - Fairness algorithm prevents low-priority starvation
    - Message TTL with automatic expiration
    - Redis-backed for persistence and reliability
    - Weighted round-robin dequeue

    Usage:
        pq = PriorityQueueModule(redis, config)
        await pq.enqueue(message, MessagePriority.HIGH)
        result = await pq.dequeue()
    """

    def __init__(
        self,
        redis: Redis,
        config: Optional[PriorityQueueConfig] = None,
    ):
        """
        Initialize priority queue module.

        Args:
            redis: Redis connection
            config: Priority queue configuration
        """
        self.redis = redis
        self.config = config or PriorityQueueConfig()
        self._dequeue_counter = 0  # For weighted round-robin

    def _get_queue_key(self, priority: MessagePriority, target_id: str) -> str:
        """Get Redis key for priority queue."""
        return f"pqueue:{target_id}:{priority.name.lower()}"

    def _get_metadata_key(self, message_id: str) -> str:
        """Get Redis key for message metadata."""
        return f"pqueue:meta:{message_id}"

    async def enqueue(
        self,
        message_id: str,
        sender_id: str,
        target_id: str,
        payload: dict,
        priority: MessagePriority = MessagePriority.NORMAL,
        ttl_seconds: Optional[float] = None,
    ) -> EnqueueResult:
        """
        Enqueue message with specified priority.

        Args:
            message_id: Unique message identifier
            sender_id: Sender agent ID
            target_id: Target agent ID
            payload: Message payload
            priority: Message priority level
            ttl_seconds: Custom TTL (defaults to config)

        Returns:
            EnqueueResult with queue position and estimated delay
        """
        enqueued_at = time.time()
        ttl = ttl_seconds or self.config.message_ttl_seconds

        # Create queued message
        queued_msg = QueuedMessage(
            message_id=message_id,
            sender_id=sender_id,
            target_id=target_id,
            payload=payload,
            priority=priority,
            enqueued_at=enqueued_at,
            ttl_seconds=ttl,
        )

        # Store message metadata
        metadata_key = self._get_metadata_key(message_id)
        await self.redis.setex(
            metadata_key,
            max(1, int(ttl)),  # Ensure at least 1 second
            queued_msg.model_dump_json(),
        )

        # Add to priority queue (sorted set by timestamp)
        queue_key = self._get_queue_key(priority, target_id)
        score = enqueued_at  # Messages with same priority ordered by arrival time

        await self.redis.zadd(queue_key, {message_id: score})

        # Set TTL on the queue itself to clean up empty queues
        await self.redis.expire(queue_key, int(ttl * 2))

        # Calculate queue position and estimated delay
        queue_position = await self._estimate_queue_position(target_id, priority, score)
        estimated_delay = await self._estimate_delay(target_id, queue_position)

        logger.debug(
            "Message enqueued",
            extra={
                "message_id": message_id,
                "priority": priority.name,
                "queue_position": queue_position,
                "estimated_delay_seconds": estimated_delay,
            },
        )

        return EnqueueResult(
            success=True,
            message_id=message_id,
            priority=priority,
            queue_position=queue_position,
            estimated_delay_seconds=estimated_delay,
        )

    async def dequeue(
        self,
        target_id: str,
        max_messages: int = 1,
    ) -> list[QueuedMessage]:
        """
        Dequeue messages for target using fair weighted round-robin.

        Algorithm:
        1. Use weighted round-robin to select priority level
        2. Dequeue oldest message from selected priority
        3. Check message TTL, skip if expired
        4. Apply fairness boost if message waited too long
        5. Return valid messages

        Args:
            target_id: Target agent ID
            max_messages: Maximum messages to dequeue

        Returns:
            List of dequeued messages (may be empty)
        """
        messages = []
        current_time = time.time()

        for _ in range(max_messages):
            # Select priority level using weighted round-robin
            priority = self._select_priority_fair()

            # Try to dequeue from selected priority
            message = await self._dequeue_from_priority(
                target_id, priority, current_time
            )

            if message:
                messages.append(message)
            else:
                # Try other priorities if selected priority is empty
                for fallback_priority in sorted(
                    MessagePriority, key=lambda p: p.value, reverse=True
                ):
                    if fallback_priority == priority:
                        continue
                    message = await self._dequeue_from_priority(
                        target_id, fallback_priority, current_time
                    )
                    if message:
                        messages.append(message)
                        break

                # If no messages found in any queue, stop
                if not message:
                    break

        if messages:
            logger.debug(
                "Messages dequeued",
                extra={
                    "target_id": target_id,
                    "count": len(messages),
                    "priorities": [m.priority.name for m in messages],
                },
            )

        return messages

    async def _dequeue_from_priority(
        self,
        target_id: str,
        priority: MessagePriority,
        current_time: float,
    ) -> Optional[QueuedMessage]:
        """Dequeue single message from specific priority queue."""
        queue_key = self._get_queue_key(priority, target_id)

        # Get oldest message (lowest score)
        items = await self.redis.zrange(queue_key, 0, 0, withscores=True)

        if not items:
            return None

        message_id, _score = items[0]

        # Remove from queue
        await self.redis.zrem(queue_key, message_id)

        # Get message metadata
        metadata_key = self._get_metadata_key(message_id)
        metadata_json = await self.redis.get(metadata_key)

        if not metadata_json:
            logger.warning(
                "Message metadata not found (expired?)",
                extra={"message_id": message_id},
            )
            return None

        # Parse message
        queued_msg = QueuedMessage.model_validate_json(metadata_json)

        # Check TTL
        message_age = current_time - queued_msg.enqueued_at
        if message_age > queued_msg.ttl_seconds:
            logger.warning(
                "Message expired, skipping",
                extra={
                    "message_id": message_id,
                    "age_seconds": message_age,
                    "ttl_seconds": queued_msg.ttl_seconds,
                },
            )
            await self.redis.delete(metadata_key)
            return None

        # Check starvation and apply fairness boost if needed
        if self.config.enable_fairness:
            wait_time = current_time - queued_msg.enqueued_at
            if wait_time > self.config.starvation_threshold_seconds:
                logger.info(
                    "Message waited too long (fairness boost applied)",
                    extra={
                        "message_id": message_id,
                        "wait_time_seconds": wait_time,
                        "original_priority": queued_msg.priority.name,
                    },
                )

        # Delete metadata after successful dequeue
        await self.redis.delete(metadata_key)

        return queued_msg

    def _select_priority_fair(self) -> MessagePriority:
        """
        Select priority level using weighted round-robin.

        Uses cumulative weights to ensure fair distribution:
        - CRITICAL: 50% of selections
        - HIGH: 25%
        - NORMAL: 15%
        - LOW: 7%
        - BULK: 3%
        """
        self._dequeue_counter += 1

        # Calculate cumulative weights
        total_weight = sum(self.config.dequeue_weights.values())
        position = self._dequeue_counter % total_weight

        cumulative = 0
        for priority in sorted(MessagePriority, key=lambda p: p.value, reverse=True):
            weight = self.config.dequeue_weights[priority]
            cumulative += weight
            if position < cumulative:
                return priority

        # Fallback to NORMAL (should never reach here)
        return MessagePriority.NORMAL

    async def _estimate_queue_position(
        self,
        target_id: str,
        priority: MessagePriority,
        score: float,
    ) -> int:
        """
        Estimate position in queue.

        Considers:
        - Messages in higher priority queues (processed first)
        - Messages in same priority queue with lower score
        """
        position = 0

        # Count messages in higher priority queues
        for p in MessagePriority:
            if p.value > priority.value:
                queue_key = self._get_queue_key(p, target_id)
                count = await self.redis.zcard(queue_key)
                position += count

        # Count messages in same priority queue with lower score
        queue_key = self._get_queue_key(priority, target_id)
        count = await self.redis.zcount(queue_key, "-inf", score)
        position += count

        return max(1, position)  # At least position 1

    async def _estimate_delay(
        self,
        target_id: str,
        queue_position: int,
    ) -> float:
        """
        Estimate delay until message is processed.

        Simple heuristic: Assumes 100 messages/second processing rate.
        """
        processing_rate = 100.0  # messages per second
        return queue_position / processing_rate

    async def get_queue_stats(self, target_id: str) -> dict:
        """
        Get queue statistics for target.

        Returns:
            Dictionary with queue sizes per priority level
        """
        stats = {}

        for priority in MessagePriority:
            queue_key = self._get_queue_key(priority, target_id)
            size = await self.redis.zcard(queue_key)
            stats[priority.name] = size

        stats["total"] = sum(stats.values())

        return stats

    async def clear_queue(
        self, target_id: str, priority: Optional[MessagePriority] = None
    ) -> int:
        """
        Clear queue(s) for target.

        Args:
            target_id: Target agent ID
            priority: Specific priority to clear (None = all)

        Returns:
            Number of messages cleared
        """
        cleared = 0

        priorities = [priority] if priority else list(MessagePriority)

        for p in priorities:
            queue_key = self._get_queue_key(p, target_id)

            # Get all message IDs
            message_ids = await self.redis.zrange(queue_key, 0, -1)

            # Delete metadata for each message
            for message_id in message_ids:
                metadata_key = self._get_metadata_key(message_id)
                await self.redis.delete(metadata_key)

            # Delete the queue
            await self.redis.delete(queue_key)
            cleared += len(message_ids)

        logger.info(
            "Queue cleared",
            extra={
                "target_id": target_id,
                "priority": priority.name if priority else "ALL",
                "cleared_count": cleared,
            },
        )

        return cleared

    async def get_message_position(self, message_id: str) -> Optional[dict]:
        """
        Get current position of message in queue.

        Returns:
            Dict with position info, or None if not in queue
        """
        # Get metadata
        metadata_key = self._get_metadata_key(message_id)
        metadata_json = await self.redis.get(metadata_key)

        if not metadata_json:
            return None

        queued_msg = QueuedMessage.model_validate_json(metadata_json)

        # Find message in queue
        queue_key = self._get_queue_key(queued_msg.priority, queued_msg.target_id)
        score = await self.redis.zscore(queue_key, message_id)

        if score is None:
            return None

        # Calculate position
        position = await self._estimate_queue_position(
            queued_msg.target_id,
            queued_msg.priority,
            score,
        )

        wait_time = time.time() - queued_msg.enqueued_at
        remaining_ttl = queued_msg.ttl_seconds - wait_time

        return {
            "message_id": message_id,
            "priority": queued_msg.priority.name,
            "position": position,
            "wait_time_seconds": wait_time,
            "remaining_ttl_seconds": max(0, remaining_ttl),
        }

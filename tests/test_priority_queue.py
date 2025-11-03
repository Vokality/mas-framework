"""Tests for Priority Queue module."""

import asyncio
import pytest
from redis.asyncio import Redis
from mas.gateway.priority_queue import (
    PriorityQueueModule,
    MessagePriority,
)

# Use anyio for async test support
pytestmark = pytest.mark.asyncio


@pytest.fixture
async def redis():
    """Create Redis connection."""
    r = Redis.from_url("redis://localhost:6379", decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
async def priority_queue(redis):
    """Create priority queue module."""
    pq = PriorityQueueModule(redis)
    yield pq
    # Cleanup: Clear all test queues
    await pq.clear_queue("test_agent")


class TestPriorityQueueBasics:
    """Test basic priority queue operations."""

    async def test_enqueue_message(self, priority_queue):
        """Test enqueuing a message."""
        result = await priority_queue.enqueue(
            message_id="msg-1",
            sender_id="agent-a",
            target_id="test_agent",
            payload={"text": "hello"},
            priority=MessagePriority.NORMAL,
        )

        assert result.success
        assert result.message_id == "msg-1"
        assert result.priority == MessagePriority.NORMAL
        assert result.queue_position >= 1

    async def test_enqueue_dequeue_single(self, priority_queue):
        """Test enqueue and dequeue a single message."""
        # Enqueue
        await priority_queue.enqueue(
            message_id="msg-1",
            sender_id="agent-a",
            target_id="test_agent",
            payload={"text": "hello"},
            priority=MessagePriority.NORMAL,
        )

        # Dequeue
        messages = await priority_queue.dequeue("test_agent", max_messages=1)

        assert len(messages) == 1
        assert messages[0].message_id == "msg-1"
        assert messages[0].sender_id == "agent-a"
        assert messages[0].target_id == "test_agent"
        assert messages[0].payload == {"text": "hello"}
        assert messages[0].priority == MessagePriority.NORMAL

    async def test_dequeue_empty_queue(self, priority_queue):
        """Test dequeuing from empty queue."""
        messages = await priority_queue.dequeue("test_agent", max_messages=1)
        assert len(messages) == 0

    async def test_multiple_messages_fifo(self, priority_queue):
        """Test multiple messages with same priority are FIFO."""
        # Enqueue 3 messages with same priority
        for i in range(3):
            await priority_queue.enqueue(
                message_id=f"msg-{i}",
                sender_id="agent-a",
                target_id="test_agent",
                payload={"index": i},
                priority=MessagePriority.NORMAL,
            )
            # Small delay to ensure different timestamps
            await asyncio.sleep(0.001)

        # Dequeue all
        messages = await priority_queue.dequeue("test_agent", max_messages=3)

        assert len(messages) == 3
        # Should be in FIFO order (0, 1, 2)
        assert messages[0].payload["index"] == 0
        assert messages[1].payload["index"] == 1
        assert messages[2].payload["index"] == 2


class TestPriorityOrdering:
    """Test priority-based ordering."""

    async def test_higher_priority_first(self, priority_queue):
        """Test that higher priority messages are dequeued first."""
        # Enqueue messages in reverse priority order
        await priority_queue.enqueue(
            message_id="msg-low",
            sender_id="agent-a",
            target_id="test_agent",
            payload={"level": "low"},
            priority=MessagePriority.LOW,
        )

        await priority_queue.enqueue(
            message_id="msg-critical",
            sender_id="agent-a",
            target_id="test_agent",
            payload={"level": "critical"},
            priority=MessagePriority.CRITICAL,
        )

        await priority_queue.enqueue(
            message_id="msg-normal",
            sender_id="agent-a",
            target_id="test_agent",
            payload={"level": "normal"},
            priority=MessagePriority.NORMAL,
        )

        # Dequeue all - should get critical first, then normal, then low
        messages = await priority_queue.dequeue("test_agent", max_messages=3)

        assert len(messages) == 3
        assert messages[0].message_id == "msg-critical"
        assert messages[1].message_id == "msg-normal"
        assert messages[2].message_id == "msg-low"

    async def test_all_priority_levels(self, priority_queue):
        """Test all priority levels in correct order."""
        # Enqueue in random order
        for p in [
            MessagePriority.NORMAL,
            MessagePriority.CRITICAL,
            MessagePriority.BULK,
            MessagePriority.HIGH,
            MessagePriority.LOW,
        ]:
            await priority_queue.enqueue(
                message_id=f"msg-{p.name}",
                sender_id="agent-a",
                target_id="test_agent",
                payload={"priority": p.name},
                priority=p,
            )

        # Dequeue all - should be in descending priority order
        messages = await priority_queue.dequeue("test_agent", max_messages=5)

        assert len(messages) == 5
        assert messages[0].priority == MessagePriority.CRITICAL
        assert messages[1].priority == MessagePriority.HIGH
        assert messages[2].priority == MessagePriority.NORMAL
        assert messages[3].priority == MessagePriority.LOW
        assert messages[4].priority == MessagePriority.BULK


class TestFairness:
    """Test fairness algorithm to prevent starvation."""

    async def test_weighted_round_robin(self, priority_queue):
        """Test that weighted round-robin prevents starvation."""
        # Enqueue many messages at each priority
        for p in MessagePriority:
            for i in range(10):
                await priority_queue.enqueue(
                    message_id=f"msg-{p.name}-{i}",
                    sender_id="agent-a",
                    target_id="test_agent",
                    payload={"priority": p.name, "index": i},
                    priority=p,
                )

        # Dequeue 50 messages (need enough to reach BULK with 3% weight)
        messages = await priority_queue.dequeue("test_agent", max_messages=50)

        # Count by priority
        priority_counts = {p: 0 for p in MessagePriority}
        for msg in messages:
            priority_counts[msg.priority] += 1

        # Critical should have most, bulk should have some (not starved)
        assert (
            priority_counts[MessagePriority.CRITICAL]
            >= priority_counts[MessagePriority.HIGH]
        )
        assert (
            priority_counts[MessagePriority.HIGH]
            >= priority_counts[MessagePriority.NORMAL]
        )
        assert priority_counts[MessagePriority.BULK] > 0  # Not starved!

    async def test_low_priority_eventually_processed(self, priority_queue):
        """Test that low priority messages eventually get processed."""
        # Enqueue 1 bulk message
        await priority_queue.enqueue(
            message_id="msg-bulk",
            sender_id="agent-a",
            target_id="test_agent",
            payload={"text": "bulk"},
            priority=MessagePriority.BULK,
        )

        # Enqueue many high priority messages
        for i in range(100):
            await priority_queue.enqueue(
                message_id=f"msg-high-{i}",
                sender_id="agent-a",
                target_id="test_agent",
                payload={"index": i},
                priority=MessagePriority.HIGH,
            )

        # Dequeue many messages
        messages = await priority_queue.dequeue("test_agent", max_messages=200)

        # Bulk message should eventually appear
        bulk_found = any(m.message_id == "msg-bulk" for m in messages)
        assert bulk_found, "Bulk message was starved!"


class TestMessageTTL:
    """Test message time-to-live."""

    async def test_message_expires(self, priority_queue):
        """Test that expired messages are skipped."""
        # Enqueue message with very short TTL
        await priority_queue.enqueue(
            message_id="msg-expire",
            sender_id="agent-a",
            target_id="test_agent",
            payload={"text": "expire me"},
            priority=MessagePriority.NORMAL,
            ttl_seconds=0.1,  # 100ms
        )

        # Wait for expiration
        await asyncio.sleep(0.2)

        # Try to dequeue - should be empty
        messages = await priority_queue.dequeue("test_agent", max_messages=1)
        assert len(messages) == 0

    async def test_message_ttl_not_expired(self, priority_queue):
        """Test that non-expired messages are delivered."""
        # Enqueue message with long TTL
        await priority_queue.enqueue(
            message_id="msg-ok",
            sender_id="agent-a",
            target_id="test_agent",
            payload={"text": "still valid"},
            priority=MessagePriority.NORMAL,
            ttl_seconds=10.0,
        )

        # Dequeue immediately
        messages = await priority_queue.dequeue("test_agent", max_messages=1)

        assert len(messages) == 1
        assert messages[0].message_id == "msg-ok"


class TestQueueStats:
    """Test queue statistics."""

    async def test_get_queue_stats(self, priority_queue):
        """Test getting queue statistics."""
        # Enqueue messages at different priorities
        await priority_queue.enqueue(
            message_id="msg-1",
            sender_id="agent-a",
            target_id="test_agent",
            payload={},
            priority=MessagePriority.CRITICAL,
        )

        await priority_queue.enqueue(
            message_id="msg-2",
            sender_id="agent-a",
            target_id="test_agent",
            payload={},
            priority=MessagePriority.CRITICAL,
        )

        await priority_queue.enqueue(
            message_id="msg-3",
            sender_id="agent-a",
            target_id="test_agent",
            payload={},
            priority=MessagePriority.NORMAL,
        )

        # Get stats
        stats = await priority_queue.get_queue_stats("test_agent")

        assert stats["CRITICAL"] == 2
        assert stats["NORMAL"] == 1
        assert stats["HIGH"] == 0
        assert stats["total"] == 3

    async def test_clear_queue_all(self, priority_queue):
        """Test clearing all queues."""
        # Enqueue messages at different priorities
        for p in [
            MessagePriority.CRITICAL,
            MessagePriority.NORMAL,
            MessagePriority.LOW,
        ]:
            await priority_queue.enqueue(
                message_id=f"msg-{p.name}",
                sender_id="agent-a",
                target_id="test_agent",
                payload={},
                priority=p,
            )

        # Clear all
        cleared = await priority_queue.clear_queue("test_agent")
        assert cleared == 3

        # Stats should be empty
        stats = await priority_queue.get_queue_stats("test_agent")
        assert stats["total"] == 0

    async def test_clear_queue_specific_priority(self, priority_queue):
        """Test clearing specific priority queue."""
        # Enqueue messages at different priorities
        await priority_queue.enqueue(
            message_id="msg-critical",
            sender_id="agent-a",
            target_id="test_agent",
            payload={},
            priority=MessagePriority.CRITICAL,
        )

        await priority_queue.enqueue(
            message_id="msg-normal",
            sender_id="agent-a",
            target_id="test_agent",
            payload={},
            priority=MessagePriority.NORMAL,
        )

        # Clear only CRITICAL
        cleared = await priority_queue.clear_queue(
            "test_agent", MessagePriority.CRITICAL
        )
        assert cleared == 1

        # Stats should show only NORMAL remaining
        stats = await priority_queue.get_queue_stats("test_agent")
        assert stats["CRITICAL"] == 0
        assert stats["NORMAL"] == 1
        assert stats["total"] == 1


class TestMessagePosition:
    """Test message position tracking."""

    async def test_get_message_position(self, priority_queue):
        """Test getting message position in queue."""
        # Enqueue messages
        await priority_queue.enqueue(
            message_id="msg-1",
            sender_id="agent-a",
            target_id="test_agent",
            payload={},
            priority=MessagePriority.NORMAL,
        )

        await priority_queue.enqueue(
            message_id="msg-2",
            sender_id="agent-a",
            target_id="test_agent",
            payload={},
            priority=MessagePriority.NORMAL,
        )

        # Get position of msg-2
        position = await priority_queue.get_message_position("msg-2")

        assert position is not None
        assert position["message_id"] == "msg-2"
        assert position["priority"] == "NORMAL"
        assert position["position"] >= 1

    async def test_get_position_nonexistent(self, priority_queue):
        """Test getting position of nonexistent message."""
        position = await priority_queue.get_message_position("nonexistent")
        assert position is None


class TestMultipleTargets:
    """Test multiple target agents."""

    async def test_separate_queues_per_target(self, priority_queue):
        """Test that each target has separate queues."""
        # Enqueue for target A
        await priority_queue.enqueue(
            message_id="msg-a",
            sender_id="sender-1",
            target_id="target-a",
            payload={"target": "a"},
            priority=MessagePriority.NORMAL,
        )

        # Enqueue for target B
        await priority_queue.enqueue(
            message_id="msg-b",
            sender_id="sender-1",
            target_id="target-b",
            payload={"target": "b"},
            priority=MessagePriority.NORMAL,
        )

        # Dequeue from target A
        messages_a = await priority_queue.dequeue("target-a", max_messages=1)
        assert len(messages_a) == 1
        assert messages_a[0].message_id == "msg-a"

        # Dequeue from target B
        messages_b = await priority_queue.dequeue("target-b", max_messages=1)
        assert len(messages_b) == 1
        assert messages_b[0].message_id == "msg-b"

        # Cleanup
        await priority_queue.clear_queue("target-a")
        await priority_queue.clear_queue("target-b")

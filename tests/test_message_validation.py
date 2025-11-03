import pytest
from datetime import UTC, datetime, timedelta

from mas.protocol import Message, MessageType
from mas.transport.redis import RedisTransport, DeliveryState


def make_message(ts: datetime | None = None) -> Message:
    return Message(
        sender_id="alice",
        target_id="bob",
        message_type=MessageType.AGENT_MESSAGE,
        payload={"x": 1},
        timestamp=ts or datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_validate_message_rejects_expired():
    transport = RedisTransport()
    old_ts = datetime.now(UTC) - timedelta(minutes=10)
    msg = make_message(ts=old_ts)
    with pytest.raises(ValueError):
        await transport.validate_message(msg)


@pytest.mark.asyncio
async def test_validate_message_rejects_duplicate_within_window():
    transport = RedisTransport()
    msg = make_message()

    # Pre-populate a recent delivery for same message
    ds = DeliveryState(
        message_id=str(msg.id),
        delivery_id="d1",
        sender_id=msg.sender_id,
        target_id=msg.target_id,
        timestamp=datetime.now(UTC),
    )
    transport._deliveries["d1"] = ds  # type: ignore[attr-defined]

    with pytest.raises(ValueError):
        await transport.validate_message(msg)


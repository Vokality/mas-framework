import asyncio
import pytest

from mas.transport.service import TransportService
from mas.protocol import Message, MessageType


@pytest.mark.integration
@pytest.mark.asyncio
async def test_message_passing():
    transport = TransportService()

    # Try to start; skip test if Redis is unavailable
    try:
        await transport.start()
    except Exception as e:
        pytest.skip(f"Skipping integration test; transport start failed: {e}")

    # Send a test message
    message = Message(
        sender_id="test_sender",
        target_id="test_target",
        message_type=MessageType.AGENT_MESSAGE,
        payload={"test": "data"},
    )

    # Subscribe to test_target channel
    async with transport.message_stream("test_subscriber", "test_target") as stream:
        # Send the message
        await transport.send_message(message)

        # Receive the message with timeout
        received_message = await asyncio.wait_for(stream.__anext__(), timeout=5.0)
        assert received_message.sender_id == "test_sender"
        assert received_message.payload == {"test": "data"}

    await transport.stop()


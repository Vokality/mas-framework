import asyncio
from mas.transport.service import TransportService
from mas.protocol import Message, MessageType

async def test_message_passing():
    transport = TransportService()
    await transport.start()

    # Send a test message
    message = Message(
        sender_id="test_sender",
        target_id="test_target",
        message_type=MessageType.AGENT_MESSAGE,
        payload={"test": "data"}
    )

    # Subscribe to test_target channel
    async with transport.message_stream("test_subscriber", "test_target") as stream:
        # Send the message
        await transport.send_message(message)

        # Receive the message with timeout
        try:
            received_message = await asyncio.wait_for(stream.__anext__(), timeout=5.0)
            print(f"Received: {received_message}")
            assert received_message.sender_id == "test_sender"
            assert received_message.payload == {"test": "data"}
        except asyncio.TimeoutError:
            print("Timeout waiting for message")
            raise

    await transport.stop()
    print("Message passing test passed")

if __name__ == "__main__":
    asyncio.run(test_message_passing())
import asyncio
import pytest
from mas.sdk.agent import Agent
from mas.sdk.state import AgentState
from mas.protocol import Message, MessageType
from mas.transport.service import TransportService
from mas.sdk.runtime import AgentRuntime
from mas.persistence.memory import MemoryPersistenceProvider
from mas.mas import MAS


class DemoReceiverAgent(Agent):
    agent_id = "receiver"
    metadata = {}
    capabilities = {"receive"}
    state_model = AgentState

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.received: list[Message] = []
        self._seen = asyncio.Event()

    async def on_message(self, message: Message) -> None:
        self.received.append(message)
        self._seen.set()
        # Echo back to sender if requested
        if message.message_type == MessageType.AGENT_MESSAGE and message.payload.get("echo"):
            await self.send_message(
                Message(
                    sender_id=self.id,
                    target_id=message.sender_id,
                    message_type=MessageType.AGENT_MESSAGE,
                    payload={"reply": True},
                )
            )


class DemoSenderAgent(Agent):
    agent_id = "sender"
    metadata = {}
    capabilities = {"send"}
    state_model = AgentState

    async def on_message(self, message: Message) -> None:
        # Sender does not echo in this test
        return


@pytest.mark.integration
@pytest.mark.asyncio
async def test_agent_to_agent_message_passing():
    transport = TransportService()
    persistence = MemoryPersistenceProvider()

    await transport.start()
    await persistence.initialize()

    # Start MAS core service
    mas = MAS(transport, persistence)
    await mas.start()

    try:
        # Build runtimes and agents directly
        recv_rt = AgentRuntime(
            agent_id=DemoReceiverAgent.agent_id,
            transport=transport,
            persistence=persistence,
            capabilities=set(),
            metadata={},
        )
        send_rt = AgentRuntime(
            agent_id=DemoSenderAgent.agent_id,
            transport=transport,
            persistence=persistence,
            capabilities=set(),
            metadata={},
        )

        recv = DemoReceiverAgent(recv_rt)
        send = DemoSenderAgent(send_rt)
        await recv.start()
        await send.start()

        # sender -> receiver
        await send.send_message(
            Message(
                sender_id=send.id,
                target_id=recv.id,
                message_type=MessageType.AGENT_MESSAGE,
                payload={"k": "v"},
            )
        )

        # Wait for receiver to observe message
        await asyncio.wait_for(recv._seen.wait(), timeout=5.0)
        assert len(recv.received) == 1
        assert recv.received[0].payload == {"k": "v"}

        # Cleanup agents
        # Let MAS coordinate agent shutdown
    finally:
        await mas.stop()
        await persistence.cleanup()

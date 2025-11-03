import asyncio
import pytest

from mas.transport.base import BaseTransport
from mas.transport.service import TransportService, ServiceState
from mas.protocol import Message, MessageType


class DummyTransport(BaseTransport):
    def __init__(self) -> None:
        self.initialized = False
        self.cleaned = False
        self.subscribed = set()
        self.unsubscribed = set()

    async def initialize(self) -> None:  # type: ignore[override]
        self.initialized = True

    async def publish(self, message: Message) -> None:  # type: ignore[override]
        # No-op: for this test we only care about TransportService state checks
        return

    async def subscribe(self, channel: str) -> None:  # type: ignore[override]
        self.subscribed.add(channel)

    async def unsubscribe(self, channel: str) -> None:  # type: ignore[override]
        self.unsubscribed.add(channel)

    async def cleanup(self) -> None:  # type: ignore[override]
        self.cleaned = True

    async def get_message_stream(self, channel: str):  # type: ignore[override]
        # Minimal async generator to satisfy interface; not used in this test
        if False:
            yield  # pragma: no cover


def make_message() -> Message:
    return Message(
        sender_id="unit_sender",
        target_id="unit_target",
        message_type=MessageType.AGENT_MESSAGE,
        payload={"ok": True},
    )


@pytest.mark.asyncio
async def test_send_message_requires_running_state():
    service = TransportService(transport=DummyTransport())
    msg = make_message()

    # Service not started yet → must raise
    with pytest.raises(RuntimeError):
        await service.send_message(msg)


@pytest.mark.asyncio
async def test_service_start_and_stop_transitions():
    t = DummyTransport()
    service = TransportService(transport=t)

    assert service.state == ServiceState.INITIALIZED

    await service.start()
    assert service.state == ServiceState.RUNNING
    assert t.initialized is True

    await service.stop()
    assert service.state == ServiceState.SHUTDOWN
    assert t.cleaned is True

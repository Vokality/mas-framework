"""Regression tests for graceful shutdown draining.

stop() must let in-flight handlers finish and flush queued ACK/NACK events
before the transport is cancelled; otherwise the server redelivers messages
whose work already completed. After the transport is cancelled, any handler
that slipped in during the drain window must be cancelled, not orphaned.
"""

from __future__ import annotations

import asyncio

import pytest
from mas_agent import Agent
from mas_proto.runtime.v1 import runtime_pb2 as mas_pb2


def _ack(delivery_id: str) -> mas_pb2.ClientEvent:
    return mas_pb2.ClientEvent(ack=mas_pb2.Ack(delivery_id=delivery_id))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_drain_outgoing_flushes_queued_events() -> None:
    agent = Agent("sender")
    drained: list[str] = []

    async def consumer() -> None:
        while True:
            event = await agent._outgoing.get()
            drained.append(event.ack.delivery_id)

    agent._transport_task = asyncio.create_task(consumer())
    try:
        for i in range(3):
            await agent._outgoing.put(_ack(f"d{i}"))

        await agent._drain_outgoing(timeout=1.0)

        assert agent._outgoing.empty()
        assert drained == ["d0", "d1", "d2"]
    finally:
        agent._transport_task.cancel()
        await asyncio.gather(agent._transport_task, return_exceptions=True)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_drain_outgoing_noop_without_live_transport() -> None:
    agent = Agent("sender")
    # No transport task running: draining must return immediately, not block on
    # a queue nobody is consuming.
    await agent._outgoing.put(_ack("d0"))

    await asyncio.wait_for(agent._drain_outgoing(timeout=5.0), timeout=0.5)

    assert not agent._outgoing.empty()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_drain_outgoing_breaks_early_when_transport_dies() -> None:
    agent = Agent("sender")

    async def dies_immediately() -> None:
        return

    agent._transport_task = asyncio.create_task(dies_immediately())
    await agent._transport_task
    # Queue is non-empty and the transport is done: must return promptly rather
    # than spinning the full timeout budget.
    await agent._outgoing.put(_ack("d0"))

    await asyncio.wait_for(agent._drain_outgoing(timeout=5.0), timeout=0.5)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_drain_handler_tasks_waits_for_completion() -> None:
    agent = Agent("sender")
    completed = asyncio.Event()

    async def handler() -> None:
        await asyncio.sleep(0.02)
        await agent._outgoing.put(_ack("done"))
        completed.set()

    task = asyncio.create_task(handler())
    agent._handler_tasks.add(task)
    task.add_done_callback(agent._handler_tasks.discard)

    await agent._drain_handler_tasks()

    assert completed.is_set()
    assert not task.cancelled()
    assert agent._outgoing.qsize() == 1
    assert agent._handler_tasks == set()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_drain_handler_tasks_leaves_stragglers_until_transport_stops() -> None:
    agent = Agent("sender")

    async def stuck() -> None:
        await asyncio.sleep(100)

    task = asyncio.create_task(stuck())
    agent._handler_tasks.add(task)
    task.add_done_callback(agent._handler_tasks.discard)

    await agent._drain_handler_tasks(timeout=0.05)

    assert not task.done()
    assert task in agent._handler_tasks

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_handler_tasks_cancels_drain_window_stragglers() -> None:
    # Simulates a handler spawned during the drain window: it must be cancelled
    # after the transport stops, never orphaned past shutdown.
    agent = Agent("sender")

    async def stuck() -> None:
        await asyncio.sleep(100)

    task = asyncio.create_task(stuck())
    agent._handler_tasks.add(task)
    task.add_done_callback(agent._handler_tasks.discard)

    await agent._cancel_handler_tasks()

    assert task.cancelled()
    assert agent._handler_tasks == set()

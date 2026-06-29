from __future__ import annotations

import asyncio
import json
import time
from typing import cast

import pytest
from mas_core import EnvelopeMessage
from mas_proto.runtime.v1 import runtime_pb2 as mas_pb2
from mas_server.errors import InvalidArgumentError, PermissionDeniedError
from mas_server.ingress import IngressService
from mas_server.policy import PolicyPipeline
from mas_server.sessions import SessionManager
from mas_server.types import AgentDefinition, InflightDelivery

pytestmark = pytest.mark.asyncio


async def _idle_task() -> None:
    await asyncio.Event().wait()


def _task_factory(
    _agent_id: str,
    _instance_id: str,
    _outbound: asyncio.Queue[mas_pb2.ServerEvent],
    _inflight: dict[str, InflightDelivery],
) -> asyncio.Task[None]:
    return asyncio.create_task(_idle_task())


async def _connect_sessions(*agent_ids: str) -> SessionManager:
    sessions = SessionManager(
        agents={
            agent_id: AgentDefinition(agent_id=agent_id, capabilities=[], metadata={})
            for agent_id in agent_ids
        }
    )
    for agent_id in agent_ids:
        await sessions.connect(
            agent_id=agent_id,
            instance_id=f"{agent_id}-inst",
            task_factory=_task_factory,
        )
    return sessions


async def _close_sessions(sessions: SessionManager) -> None:
    active = await sessions.snapshot_and_clear()
    for session in active:
        session.task.cancel()
    await asyncio.gather(*(session.task for session in active), return_exceptions=True)


class _DenyPolicy:
    async def ingest_and_route(self, _message: EnvelopeMessage) -> None:
        raise PermissionDeniedError("not_authorized")


class _SlowPolicy:
    def __init__(self) -> None:
        self.calls = 0

    async def ingest_and_route(self, _message: EnvelopeMessage) -> None:
        self.calls += 1
        if self.calls == 1:
            await asyncio.sleep(0.05)


async def test_failed_request_routing_deletes_pending_correlation(redis) -> None:
    sessions = await _connect_sessions("sender")
    service = IngressService(
        sessions=sessions,
        policy=cast(PolicyPipeline, _DenyPolicy()),
        redis=redis,
    )

    try:
        with pytest.raises(PermissionDeniedError):
            await service.request_message(
                sender_id="sender",
                sender_instance_id="sender-inst",
                target_id="worker",
                message_type="question",
                data_json="{}",
                timeout_ms=5000,
            )

        pending = [key async for key in redis.scan_iter("mas.pending_request:*")]
        assert pending == []
    finally:
        await _close_sessions(sessions)


async def test_concurrent_replies_reserve_correlation_once(redis) -> None:
    sessions = await _connect_sessions("responder")
    policy = _SlowPolicy()
    service = IngressService(
        sessions=sessions,
        policy=cast(PolicyPipeline, policy),
        redis=redis,
    )
    pending_key = "mas.pending_request:concurrent-reply"
    await redis.set(
        pending_key,
        json.dumps(
            {
                "agent_id": "requester",
                "instance_id": "requester-inst",
                "target_id": "responder",
                "expires_at": time.time() + 10,
            }
        ),
        ex=10,
    )

    async def reply() -> str:
        return await service.reply_message(
            sender_id="responder",
            sender_instance_id="responder-inst",
            correlation_id="concurrent-reply",
            message_type="answer",
            data_json="{}",
        )

    try:
        results = await asyncio.gather(reply(), reply(), return_exceptions=True)

        successes = [result for result in results if isinstance(result, str)]
        failures = [
            result for result in results if isinstance(result, InvalidArgumentError)
        ]
        assert len(successes) == 1
        assert len(failures) == 1
        assert policy.calls == 1
        assert await redis.exists(pending_key) == 0
    finally:
        await redis.delete(pending_key)
        await _close_sessions(sessions)

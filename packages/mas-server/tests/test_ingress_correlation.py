from __future__ import annotations

import asyncio
import json
import time

import pytest
from mas_core import EnvelopeMessage
from mas_gateway.audit import AuditModule
from mas_gateway.authorization import AuthorizationModule
from mas_gateway.rate_limit import RateLimitModule
from mas_server.errors import PermissionDeniedError
from mas_server.ingress import IngressService
from mas_server.policy import PolicyPipeline
from mas_server.routing import CorrelationCommit, MessageRouter
from mas_server.sessions import SessionManager
from mas_server.types import AgentDefinition, InflightDelivery, OutboundDelivery
from redis.asyncio import Redis

pytestmark = pytest.mark.asyncio


async def _idle_task() -> None:
    await asyncio.Event().wait()


def _task_factory(
    _agent_id: str,
    _instance_id: str,
    _outbound: asyncio.Queue[OutboundDelivery],
    _inflight: dict[str, InflightDelivery],
) -> asyncio.Task[None]:
    return asyncio.create_task(_idle_task())


async def _connect_sessions(redis: Redis, *agent_ids: str) -> SessionManager:
    sessions = SessionManager(
        redis=redis,
        agents={
            agent_id: AgentDefinition(agent_id=agent_id, capabilities=[], metadata={})
            for agent_id in agent_ids
        },
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


class _DenyPolicy(PolicyPipeline):
    async def ingest_and_route(
        self,
        message: EnvelopeMessage,
        *,
        correlation: CorrelationCommit | None = None,
    ) -> str:
        raise PermissionDeniedError("not_authorized")


def _policy(redis: Redis) -> PolicyPipeline:
    return PolicyPipeline(
        authz=AuthorizationModule(redis, enable_rbac=False),
        rate_limit=RateLimitModule(
            redis, default_per_minute=100, default_per_hour=1000
        ),
        audit=AuditModule(redis, file_sink=None),
        router=MessageRouter(redis=redis, dlq_enabled=True),
        dlp=None,
        circuit_breaker=None,
    )


async def test_failed_request_routing_deletes_pending_correlation(redis: Redis) -> None:
    sessions = await _connect_sessions(redis, "sender")
    service = IngressService(
        sessions=sessions,
        policy=_DenyPolicy(
            authz=AuthorizationModule(redis, enable_rbac=False),
            rate_limit=RateLimitModule(
                redis, default_per_minute=100, default_per_hour=1000
            ),
            audit=AuditModule(redis, file_sink=None),
            router=MessageRouter(redis=redis, dlq_enabled=True),
            dlp=None,
            circuit_breaker=None,
        ),
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


async def test_concurrent_identical_replies_commit_once(redis: Redis) -> None:
    sessions = await _connect_sessions(redis, "responder", "requester")
    policy = _policy(redis)
    await AuthorizationModule(redis, enable_rbac=False).set_permissions(
        "responder", allowed_targets=["requester"]
    )
    service = IngressService(
        sessions=sessions,
        policy=policy,
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
        assert len(successes) == 2
        assert successes[0] == successes[1]
        assert await redis.xlen("agent.stream:requester:requester-inst") == 1
        assert await redis.exists(pending_key) == 0
    finally:
        await redis.delete(pending_key)
        await _close_sessions(sessions)

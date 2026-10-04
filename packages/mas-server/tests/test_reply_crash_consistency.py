"""Reply atomicity and replay after SIGKILL, using disposable AOF-backed Redis."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

import pytest
from mas_core.durability import (
    RedisDurability,
    RedisDurabilityError,
    RedisDurabilitySettings,
)
from mas_core.protocol import EnvelopeMessage
from mas_gateway.audit import AuditModule
from mas_gateway.authorization import AuthorizationModule
from mas_gateway.rate_limit import RateLimitModule
from mas_server.errors import InvalidArgumentError, PermissionDeniedError
from mas_server.ingress import IngressService
from mas_server.policy import PolicyPipeline
from mas_server.routing import CorrelationCommit, MessageRouter, ReplyReceipt
from mas_server.sessions import SessionManager
from mas_server.types import AgentDefinition, InflightDelivery, OutboundDelivery
from pydantic import TypeAdapter
from redis.asyncio import Redis
from redis.exceptions import ConnectionError, RedisError, ResponseError

_STREAM_ENTRIES = TypeAdapter(list[tuple[str, dict[str, str]]])


@dataclass(frozen=True, slots=True)
class RedisSandbox:
    client: Redis
    socket: Path


@pytest.fixture
async def sandbox() -> AsyncIterator[RedisSandbox]:
    """Keep process-crash evidence independent of the shared development database."""
    executable = shutil.which("redis-server")
    if executable is None:
        pytest.skip("redis-server is required for abrupt-process recovery coverage")
    with TemporaryDirectory(prefix="mas-reply-", dir="/tmp") as directory:
        socket = Path(directory) / "redis.sock"
        process = await asyncio.create_subprocess_exec(
            executable,
            "--port",
            "0",
            "--unixsocket",
            str(socket),
            "--unixsocketperm",
            "700",
            "--dir",
            directory,
            "--save",
            "",
            "--appendonly",
            "yes",
            "--appendfsync",
            "always",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        redis = Redis(unix_socket_path=str(socket), decode_responses=True)
        try:
            async with asyncio.timeout(5):
                while True:
                    try:
                        await redis.ping()
                        break
                    except ConnectionError:
                        await asyncio.sleep(0.01)
            yield RedisSandbox(redis, socket)
        finally:
            await redis.aclose()
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except TimeoutError:
                process.kill()
                await process.wait()


async def _idle() -> None:
    await asyncio.Event().wait()


def _worker(
    agent_id: str,
    instance_id: str,
    outbound: asyncio.Queue[OutboundDelivery],
    inflight: dict[str, InflightDelivery],
) -> asyncio.Task[None]:
    return asyncio.create_task(_idle())


@asynccontextmanager
async def _service(
    redis: Redis,
    *,
    router: MessageRouter | None = None,
    durability: RedisDurability | None = None,
    instance_id: str = "recovered",
) -> AsyncIterator[IngressService]:
    sessions = SessionManager(
        redis=redis,
        agents={
            "responder": AgentDefinition("responder", [], {}),
            "requester": AgentDefinition("requester", [], {}),
        },
    )
    await sessions.connect(
        agent_id="responder", instance_id=instance_id, task_factory=_worker
    )
    await sessions.connect(
        agent_id="requester",
        instance_id=f"target-{instance_id}",
        task_factory=_worker,
    )
    authz = AuthorizationModule(redis, enable_rbac=False)
    await authz.set_permissions("responder", allowed_targets=["requester"])
    durability = durability or RedisDurability(
        RedisDurabilitySettings(wait_for_aof=True)
    )
    audit = AuditModule(redis, file_sink=None, durability=durability)
    policy = PolicyPipeline(
        authz=authz,
        rate_limit=RateLimitModule(
            redis, default_per_minute=100, default_per_hour=1000
        ),
        audit=audit,
        router=router
        or MessageRouter(
            redis=redis,
            dlq_enabled=True,
            durability=durability,
        ),
        dlp=None,
        circuit_breaker=None,
    )
    try:
        yield IngressService(redis=redis, sessions=sessions, policy=policy)
    finally:
        active = await sessions.snapshot_and_clear()
        for session in active:
            session.task.cancel()
        await asyncio.gather(*(s.task for s in active), return_exceptions=True)
        await audit.close()


async def _pending(redis: Redis, correlation_id: str, *, ttl_ms: int = 10_000) -> str:
    value = json.dumps(
        {
            "agent_id": "requester",
            "instance_id": "requester-inst",
            "target_id": "responder",
            "expires_at": time.time() + ttl_ms / 1000,
        }
    )
    await redis.set(f"mas.pending_request:{correlation_id}", value, px=ttl_ms)
    return value


async def _reply(
    service: IngressService,
    correlation_id: str,
    *,
    instance_id: str = "recovered",
    payload: str = '{"value":42,"tags":["ok"]}',
) -> str:
    return await service.reply_message(
        sender_id="responder",
        sender_instance_id=instance_id,
        correlation_id=correlation_id,
        message_type="answer",
        data_json=payload,
    )


async def _crash_child(
    socket: Path, correlation_id: str, stage: Literal["before", "after"]
) -> None:
    redis = Redis(unix_socket_path=str(socket), decode_responses=True)

    class PausingRouter(MessageRouter):
        async def commit_reply(
            self, message: EnvelopeMessage, correlation: CorrelationCommit
        ) -> str:
            if stage == "before":
                print("ready", flush=True)
                await asyncio.Event().wait()
            result = await super().commit_reply(message, correlation)
            print("ready", flush=True)
            await asyncio.Event().wait()
            return result

    router = PausingRouter(
        redis=redis,
        dlq_enabled=True,
        durability=RedisDurability(RedisDurabilitySettings(wait_for_aof=True)),
    )
    async with _service(redis, router=router, instance_id="crashed") as service:
        await _reply(service, correlation_id, instance_id="crashed")


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["before", "after"])
async def test_sigkill_preserves_or_commits_reply_for_safe_replay(
    sandbox: RedisSandbox, stage: Literal["before", "after"]
) -> None:
    redis = sandbox.client
    correlation_id = f"process-crash-{stage}"
    original = await _pending(redis, correlation_id)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        __file__,
        "--crash-child",
        str(sandbox.socket),
        correlation_id,
        stage,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None
    try:
        async with asyncio.timeout(5):
            marker = await process.stdout.readline()
        if marker != b"ready\n":
            assert process.stderr is not None
            raise AssertionError((await process.stderr.read()).decode())
        process.kill()
        assert await process.wait() < 0
        if stage == "before":
            assert await redis.get(f"mas.pending_request:{correlation_id}") == original
            assert await redis.xlen("agent.stream:requester:requester-inst") == 0
        else:
            assert await redis.get(f"mas.pending_request:{correlation_id}") is None
            assert await redis.xlen("agent.stream:requester:requester-inst") == 1
        async with _service(redis) as service:
            recovered = await _reply(
                service, correlation_id, payload='{"tags": ["ok"], "value": 42}'
            )
            assert await _reply(service, correlation_id) == recovered
        assert await redis.xlen("agent.stream:requester:requester-inst") == 1
        raw = await redis.get(f"mas.reply_receipt:{correlation_id}")
        assert raw is not None
        receipt = ReplyReceipt.model_validate_json(raw)
        assert receipt.message_id == recovered
        assert 0 < await redis.pttl(f"mas.reply_receipt:{correlation_id}") <= 10_000
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


class UncertainDurability(RedisDurability):
    def __init__(self, failure: RedisError | None = None) -> None:
        super().__init__(RedisDurabilitySettings(wait_for_aof=True))
        self.calls = 0
        self._failure = failure or RedisDurabilityError(
            "configured_durability_unconfirmed"
        )

    async def confirm(self, connection: Redis) -> None:
        self.calls += 1
        if self.calls == 1:
            raise self._failure
        await super().confirm(connection)


class PausedDurability(RedisDurability):
    def __init__(self) -> None:
        super().__init__(RedisDurabilitySettings(wait_for_aof=True))
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def confirm(self, connection: Redis) -> None:
        self.entered.set()
        await self.release.wait()
        await super().confirm(connection)


@pytest.mark.asyncio
async def test_uncertain_durability_replays_without_duplicate_enqueue(
    sandbox: RedisSandbox,
) -> None:

    durability = UncertainDurability()
    redis = sandbox.client
    await _pending(redis, "uncertain")
    router = MessageRouter(redis=redis, dlq_enabled=True, durability=durability)
    async with _service(redis, router=router) as service:
        with pytest.raises(RedisDurabilityError):
            await _reply(service, "uncertain")
        result = await _reply(service, "uncertain")
        assert result
        assert durability.calls == 2
    assert await redis.xlen("agent.stream:requester:requester-inst") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        RedisDurabilityError("configured_durability_unconfirmed"),
        ConnectionError("connection lost after commit"),
    ],
    ids=["durability-unconfirmed", "connection-lost"],
)
async def test_uncertain_request_commit_preserves_replyable_correlation(
    sandbox: RedisSandbox,
    failure: RedisError,
) -> None:
    redis = sandbox.client
    durability = UncertainDurability(failure)
    async with _service(redis, durability=durability) as service:
        with pytest.raises(type(failure), match=str(failure)):
            await service.request_message(
                sender_id="responder",
                sender_instance_id="recovered",
                target_id="requester",
                message_type="question",
                data_json="{}",
                timeout_ms=10_000,
            )
        assert durability.calls == 1
        requests = _STREAM_ENTRIES.validate_python(
            await redis.xrange("agent.stream:requester"), strict=True
        )
        assert len(requests) == 1
        fields = requests[0][1]
        message = EnvelopeMessage.model_validate_json(fields["envelope"])
        correlation_id = message.meta.correlation_id
        assert correlation_id is not None
        assert 0 < await redis.pttl(f"mas.pending_request:{correlation_id}") <= 10_000
        await AuthorizationModule(redis, enable_rbac=False).set_permissions(
            "requester", allowed_targets=["responder"]
        )
        assert await service.reply_message(
            sender_id="requester",
            sender_instance_id="target-recovered",
            correlation_id=correlation_id,
            message_type="answer",
            data_json="{}",
        )
        assert await redis.xlen("agent.stream:responder:recovered") == 1
        assert await redis.exists(f"mas.pending_request:{correlation_id}") == 0


@pytest.mark.asyncio
async def test_cancelled_request_after_atomic_commit_remains_replyable(
    sandbox: RedisSandbox,
) -> None:
    redis = sandbox.client
    durability = PausedDurability()
    async with _service(redis, durability=durability) as service:
        request = asyncio.create_task(
            service.request_message(
                sender_id="responder",
                sender_instance_id="recovered",
                target_id="requester",
                message_type="question",
                data_json="{}",
                timeout_ms=10_000,
            )
        )
        try:
            await asyncio.wait_for(durability.entered.wait(), timeout=2)
            requests = _STREAM_ENTRIES.validate_python(
                await redis.xrange("agent.stream:requester"), strict=True
            )
            assert len(requests) == 1
            message = EnvelopeMessage.model_validate_json(requests[0][1]["envelope"])
            correlation_id = message.meta.correlation_id
            assert correlation_id is not None
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            assert (
                0 < await redis.pttl(f"mas.pending_request:{correlation_id}") <= 10_000
            )
            durability.release.set()
            await AuthorizationModule(redis, enable_rbac=False).set_permissions(
                "requester", allowed_targets=["responder"]
            )
            assert await service.reply_message(
                sender_id="requester",
                sender_instance_id="target-recovered",
                correlation_id=correlation_id,
                message_type="answer",
                data_json="{}",
            )
            assert await redis.xlen("agent.stream:responder:recovered") == 1
            assert await redis.exists(f"mas.pending_request:{correlation_id}") == 0
        finally:
            durability.release.set()
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)


@pytest.mark.asyncio
async def test_changed_reply_content_cannot_replace_the_winner(
    sandbox: RedisSandbox,
) -> None:
    redis = sandbox.client
    await _pending(redis, "winner")
    async with _service(redis) as service:
        results = await asyncio.gather(
            _reply(service, "winner", payload='{"value":1}'),
            _reply(service, "winner", payload='{"value":2}'),
            return_exceptions=True,
        )
        assert sum(isinstance(result, str) for result in results) == 1
        assert sum(isinstance(result, InvalidArgumentError) for result in results) == 1
    assert await redis.xlen("agent.stream:requester:requester-inst") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("corrupt", ["stream", "receipt"])
async def test_invalid_redis_types_do_not_partially_consume_correlation(
    sandbox: RedisSandbox, corrupt: str
) -> None:
    redis = sandbox.client
    original = await _pending(redis, "wrong-type")
    if corrupt == "stream":
        await redis.set("agent.stream:requester:requester-inst", "wrong type")
    else:
        await redis.hset("mas.reply_receipt:wrong-type", mapping={"wrong": "type"})
    async with _service(redis) as service:
        with pytest.raises(ResponseError):
            await _reply(service, "wrong-type")
    assert await redis.get("mas.pending_request:wrong-type") == original
    if corrupt == "stream":
        assert await redis.get("agent.stream:requester:requester-inst") == "wrong type"
        assert await redis.get("mas.reply_receipt:wrong-type") is None
    else:
        assert await redis.xlen("agent.stream:requester:requester-inst") == 0


@pytest.mark.asyncio
async def test_policy_failure_preserves_correlation_without_restoring_it(
    sandbox: RedisSandbox,
) -> None:
    redis = sandbox.client
    original = await _pending(redis, "denied")
    async with _service(redis) as service:
        await AuthorizationModule(redis, enable_rbac=False).set_permissions(
            "responder", allowed_targets=[]
        )
        with pytest.raises(PermissionDeniedError):
            await _reply(service, "denied")
    assert await redis.get("mas.pending_request:denied") == original
    assert await redis.xlen("agent.stream:requester:requester-inst") == 0


if __name__ == "__main__":
    if len(sys.argv) != 5 or sys.argv[1] != "--crash-child":
        raise SystemExit("invalid crash child invocation")
    mode = sys.argv[4]
    if mode not in {"before", "after"}:
        raise SystemExit("invalid crash stage")
    asyncio.run(
        _crash_child(
            Path(sys.argv[2]), sys.argv[3], "before" if mode == "before" else "after"
        )
    )

"""Optimistic state updates never silently overwrite concurrent changes."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import grpc
import grpc.aio as grpc_aio
import pytest
from mas_agent import Agent
from mas_core.sessions import SessionLeaseStore
from mas_proto.runtime.v1 import runtime_pb2 as pb
from mas_server.errors import RpcError
from mas_server.runtime import MASServer
from mas_server.state import StateStore
from mas_server.types import AgentDefinition
from pydantic import BaseModel
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from conftest import TestTlsPaths as TlsPaths

pytestmark = pytest.mark.asyncio


class CounterState(BaseModel):
    count: int = 0


async def test_agent_state_conflict_is_explicit_and_refresh_allows_retry(
    redis: Redis,
    mas_server_factory: Callable[
        [dict[str, AgentDefinition] | None], Awaitable[MASServer]
    ],
    test_tls: TlsPaths,
) -> None:
    server = await mas_server_factory({"worker": AgentDefinition("worker", [], {})})
    first, second = (
        Agent(
            "worker",
            state_model=CounterState,
            server_addr=server.bound_addr,
            tls=test_tls.client("worker"),
        )
        for _ in range(2)
    )
    await first.start()
    await second.start()
    try:
        with pytest.raises(grpc_aio.AioRpcError) as missing:
            await first._require_stub().UpdateState(
                pb.UpdateStateRequest(updates={"count": "1"})
            )
        assert missing.value.code() == grpc.StatusCode.FAILED_PRECONDITION
        assert missing.value.details() == "state_revision_required"
        await first.update_state({"count": 1})
        with pytest.raises(grpc_aio.AioRpcError) as conflict:
            await second.update_state({"count": 1})
        assert conflict.value.code() == grpc.StatusCode.ABORTED
        assert conflict.value.details() == "state_revision_conflict"
        assert second.state.count == 0
        assert second._state_revision == 0
        await second.refresh_state()
        assert second.state.count == 1
        await second.update_state({"count": 2})
        await first.refresh_state()
        assert first.state.count == 2
        assert first._state_revision == 2
    finally:
        await second.stop()
        await first.stop()


async def test_two_writers_share_revision_and_reject_lost_update(redis: Redis) -> None:
    first, second = StateStore(redis), StateStore(redis)
    snapshots = await asyncio.gather(
        first.snapshot(agent_id="worker"), second.snapshot(agent_id="worker")
    )
    assert snapshots[0].revision == snapshots[1].revision == 0
    results = await asyncio.gather(
        first.update_state(
            agent_id="worker", updates={"count": "1"}, expected_revision=0
        ),
        second.update_state(
            agent_id="worker", updates={"count": "2"}, expected_revision=0
        ),
        return_exceptions=True,
    )
    assert results.count(1) == 1
    conflicts = [result for result in results if isinstance(result, RpcError)]
    assert len(conflicts) == 1
    assert conflicts[0].status == grpc.StatusCode.ABORTED
    latest = await second.snapshot(agent_id="worker")
    assert latest.revision == 1
    await second.update_state(
        agent_id="worker",
        updates={"count": str(int(latest.fields["count"]) + 1)},
        expected_revision=latest.revision,
    )
    assert (await first.snapshot(agent_id="worker")).revision == 2


async def test_reset_advances_revision_and_fences_pre_reset_updates(
    redis: Redis,
) -> None:
    store = StateStore(redis)
    assert (
        await store.update_state(
            agent_id="worker", updates={"count": "1"}, expected_revision=0
        )
        == 1
    )
    assert await store.reset_state(agent_id="worker", expected_revision=1) == 2
    current = await store.snapshot(agent_id="worker")
    assert current.fields == {} and current.revision == 2
    with pytest.raises(RpcError, match="state_revision_conflict"):
        await store.update_state(
            agent_id="worker", updates={"count": "99"}, expected_revision=1
        )
    assert (
        await store.update_state(
            agent_id="worker", updates={"count": "3"}, expected_revision=2
        )
        == 3
    )


@pytest.mark.parametrize("revision", ["1.5", "invalid", "9223372036854775807"])
async def test_invalid_persisted_revision_never_partially_updates_state(
    redis: Redis, revision: str
) -> None:
    await redis.hset("agent.state:worker", mapping={"count": "original"})
    await redis.set("agent.state.revision:worker", revision)
    with pytest.raises(ResponseError, match="invalid_state_revision"):
        await StateStore(redis).update_state(
            agent_id="worker", updates={"count": "changed"}, expected_revision=1
        )
    assert await redis.hget("agent.state:worker", "count") == "original"
    assert await redis.get("agent.state.revision:worker") == revision


async def test_invalid_session_index_cannot_create_partial_ownership(
    redis: Redis,
) -> None:
    await redis.set("mas.sessions:worker", "invalid-index")
    with pytest.raises(ResponseError, match="invalid_session_storage_type"):
        await SessionLeaseStore(redis).acquire("worker", "instance")
    assert await redis.get("mas.session:worker:instance") is None

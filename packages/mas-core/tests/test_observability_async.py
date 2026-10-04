"""Retained observation preparation stays off-loop and cancellation-safe."""

from __future__ import annotations

import asyncio
import time
from threading import Event, Timer, get_ident

import pytest
from mas_core import observability as observation_module
from mas_core.observability import ObservabilityStore, ObservedSpan
from redis.asyncio import Redis
from redis.typing import EncodableT


@pytest.mark.parametrize("cancel", [False, True])
async def test_retained_trace_preparation_does_not_block_or_mutate_after_cancel(
    monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    """Pause the actual persisted-trace parser without opening a Redis socket."""
    now = time.time_ns()
    current = ObservedSpan(
        trace_id=f"{100:032x}",
        span_id=f"{2:016x}",
        parent_span_id=f"{1:016x}",
        name="mas.agent.handle_message",
        service_name="mas-agent",
        started_unix_ns=now,
        finished_unix_ns=now + 1,
        attributes={"mas.message_id": "message"},
    )
    parent = current.model_copy(
        update={
            "span_id": f"{1:016x}",
            "parent_span_id": None,
            "name": "mas.agent.send",
            "started_unix_ns": now - 1_000_000,
        }
    )
    prior = observation_module._SPAN_LIST.dump_json([parent]).decode()
    incoming = observation_module._SPAN_LIST.dump_json([current]).decode()
    started, release, finished = Event(), Event(), Event()
    threads: list[int] = []
    commits = 0
    parse = observation_module._SPAN_LIST.validate_json

    def parse_spans(data: str) -> list[ObservedSpan]:
        if data != prior:
            return parse(data)
        threads.append(get_ident())
        started.set()
        try:
            if not release.wait(1):
                raise TimeoutError("test retained trace parser did not release")
            return parse(data)
        finally:
            finished.set()

    async def eval_script(script: str, numkeys: int, *args: EncodableT) -> int:
        nonlocal commits
        if script == observation_module._COMMIT:
            commits += 1
        return 1

    async def get_value(name: str) -> None:
        return None

    async def get_members(name: str) -> dict[str, str]:
        return {}

    async def get_rows(
        name: str, *, min: str, count: int
    ) -> list[tuple[str, dict[str, str]]]:
        return [("1-0", {"data": incoming})]

    async def get_traces(name: str, keys: list[str]) -> list[str]:
        return [prior]

    async def get_completed(name: str, keys: list[str]) -> list[None]:
        return [None] * len(keys)

    redis = Redis.from_url("redis://127.0.0.1:6379", decode_responses=True)
    monkeypatch.setattr(redis, "eval", eval_script)
    monkeypatch.setattr(redis, "get", get_value)
    monkeypatch.setattr(redis, "hgetall", get_members)
    monkeypatch.setattr(redis, "xrange", get_rows)
    monkeypatch.setattr(redis, "hmget", get_traces)
    monkeypatch.setattr(redis, "zmscore", get_completed)
    monkeypatch.setattr(observation_module._SPAN_LIST, "validate_json", parse_spans)
    store = ObservabilityStore(redis)
    store._checkpoint = observation_module._Checkpoint()
    escape = Timer(0.8, release.set)
    escape.start()
    flushing = asyncio.create_task(store.flush())
    try:
        assert await asyncio.to_thread(started.wait, 0.5)
        assert threads == [threads[0]]
        assert threads[0] != get_ident()
        assert not flushing.done()
        assert set(store._selected[current.trace_id]) == {current.span_id}
        if cancel:
            flushing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await flushing
        release.set()
        assert await asyncio.to_thread(finished.wait, 0.5)
        if cancel:
            assert commits == 0
            assert set(store._selected[current.trace_id]) == {current.span_id}
            assert store._checkpoint is None
        else:
            await flushing
            assert commits == 1
            assert set(store._selected[current.trace_id]) == {
                current.span_id,
                parent.span_id,
            }
    finally:
        release.set()
        escape.cancel()
        await asyncio.gather(flushing, return_exceptions=True)
        await redis.aclose()

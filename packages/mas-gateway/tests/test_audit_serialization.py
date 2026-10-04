"""Canonical prepared records preserve audit hash and stream compatibility."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from types import TracebackType
from typing import Self, TypedDict

import pytest
from mas_core.durability import RedisDurability
from mas_core.redis_commit import StreamAppend
from mas_gateway.audit import (
    AuditEntry,
    AuditModule,
    AuditWriteReceipt,
    PendingAuditWrite,
    PreparedAuditEntry,
    SecurityEvent,
)
from mas_gateway.audit_archive import AuditCapacityError
from pydantic import TypeAdapter
from redis.asyncio import Redis
from redis.typing import EncodableT, KeyT


class _Append(TypedDict):
    key: int
    fields: list[str]


class _Record(TypedDict):
    fields: list[str]
    indexes: list[int]
    append: _Append | None


@dataclass(frozen=True)
class _Attempt:
    keys: tuple[str, ...]
    previous: str
    final: str
    records: list[_Record]


_KEYS = TypeAdapter(tuple[str, ...])
_STRING = TypeAdapter(str)
_RECORDS = TypeAdapter(list[_Record])


class _CountingTuple[T](tuple[T, ...]):
    iterations: int = 0

    def __iter__(self) -> Iterator[T]:
        self.iterations += 1
        return super().__iter__()


class _ScriptedRedis(Redis):
    def __init__(self, responses: list[list[str]]) -> None:
        super().__init__(decode_responses=True)
        self.responses = deque(responses)
        self.attempts: list[_Attempt] = []
        self.borrowed = False
        self.committed = False

    def client(self) -> Self:
        return self

    async def __aenter__(self) -> Self:
        assert not self.borrowed
        self.borrowed = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.borrowed = False

    async def eval(
        self, script: str, numkeys: int, *keys_and_args: KeyT | EncodableT
    ) -> list[str]:
        assert self.borrowed
        self.attempts.append(
            _Attempt(
                _KEYS.validate_python(keys_and_args[:numkeys]),
                _STRING.validate_python(keys_and_args[numkeys]),
                _STRING.validate_python(keys_and_args[numkeys + 1]),
                _RECORDS.validate_json(
                    _STRING.validate_python(keys_and_args[numkeys + 3])
                ),
            )
        )
        response = self.responses.popleft()
        self.committed = response[0] == "ok"
        return response


class _ObservedDurability(RedisDurability):
    def __init__(self, expected: _ScriptedRedis) -> None:
        super().__init__()
        self.expected = expected
        self.calls = 0

    async def confirm(self, connection: Redis) -> None:
        assert connection is self.expected
        assert self.expected.borrowed and self.expected.committed
        self.calls += 1


def _pending_write(
    message_id: str,
    indexes: tuple[str, ...],
    append: StreamAppend,
) -> PendingAuditWrite:
    entry = AuditEntry(
        message_id=message_id,
        timestamp=1.5,
        sender_id="sender",
        target_id="target",
        decision="ALLOWED",
        latency_ms=1.0,
        payload_hash="c" * 64,
    )
    return PendingAuditWrite(
        entry,
        "audit:messages",
        indexes,
        asyncio.get_running_loop().create_future(),
        append,
        False,
        PreparedAuditEntry.from_entry(entry),
    )


@pytest.mark.parametrize("previous_hash", [None, "0" * 64, "a" * 64])
@pytest.mark.parametrize("hash_version", [1, 2])
@pytest.mark.parametrize("populated", [False, True])
def test_prepared_message_is_identical_to_existing_record_serialization(
    previous_hash: str | None, hash_version: int, populated: bool
) -> None:
    entry = AuditEntry(
        message_id='message "snowman ☃"',
        timestamp=1_700_000_000.125,
        sender_id="sender",
        sender_instance_id="instance" if populated else None,
        target_id="target",
        message_type="message.type" if populated else None,
        correlation_id="correlation" if populated else None,
        decision="ALLOWED",
        latency_ms=0.00125,
        payload_hash="b" * 64,
        violations=['line\nbreak "quote"', "☃"] if populated else [],
        previous_hash="f" * 64,
        hash_version=hash_version,
    )
    prepared = PreparedAuditEntry.from_entry(entry)
    linked = entry.model_copy(update={"previous_hash": previous_hash})
    fields, digest = prepared.linked(previous_hash)
    assert list(fields.items()) == list(
        AuditModule._entry_to_stream_fields(linked).items()
    )
    assert (
        prepared.canonical_prefix
        + json.dumps(previous_hash)
        + prepared.canonical_suffix
    ) == json.dumps(
        linked.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    assert digest == AuditModule._hash_entry(linked)
    assert entry.previous_hash == "f" * 64


@pytest.mark.parametrize("previous_hash", [None, "0" * 64, "a" * 64])
@pytest.mark.parametrize("hash_version", [1, 2])
def test_prepared_security_event_preserves_nested_values_and_canonical_order(
    previous_hash: str | None, hash_version: int
) -> None:
    entry = SecurityEvent(
        timestamp=1_700_000_000.125,
        event_type='access "☃"',
        details={
            "previous_hash": None,
            "nested": {"z": 1, "a": [None, True, "☃", {"previous_hash": None}]},
            "null": None,
            "escaped": '"previous_hash":null\n',
        },
        previous_hash="f" * 64,
        hash_version=hash_version,
    )
    prepared = PreparedAuditEntry.from_entry(entry)
    linked = entry.model_copy(update={"previous_hash": previous_hash})
    fields, digest = prepared.linked(previous_hash)
    assert list(fields.items()) == list(
        AuditModule._entry_to_stream_fields(linked).items()
    )
    assert (
        prepared.canonical_prefix
        + json.dumps(previous_hash)
        + prepared.canonical_suffix
    ) == json.dumps(
        linked.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    assert digest == AuditModule._hash_entry(linked)
    assert entry.previous_hash == "f" * 64


def test_prepared_record_can_retry_links_without_model_conversion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = SecurityEvent(timestamp=1.5, event_type="retry", details={"null": None})
    prepared = PreparedAuditEntry.from_entry(entry)
    expected: list[tuple[dict[str, str], str]] = []
    for previous in (None, "a" * 64, "b" * 64, None):
        linked = entry.model_copy(update={"previous_hash": previous})
        expected.append(
            (
                AuditModule._entry_to_stream_fields(linked),
                AuditModule._hash_entry(linked),
            )
        )

    def reject_model_conversion(*args: object, **kwargs: object) -> object:
        raise AssertionError("prepared_record_converted_again")

    monkeypatch.setattr(SecurityEvent, "model_dump", reject_model_conversion)
    monkeypatch.setattr(SecurityEvent, "model_copy", reject_model_conversion)
    assert [
        prepared.linked(previous) for previous in (None, "a" * 64, "b" * 64, None)
    ] == expected


@pytest.mark.asyncio
async def test_cas_retries_reuse_immutable_effects_and_relink_receipts() -> None:
    redis = _ScriptedRedis(
        [["retry", "a" * 64], ["retry", "b" * 64], ["ok", "10-0", "10-1"]]
    )
    durability = _ObservedDurability(redis)
    audit = AuditModule(redis, file_sink=None, durability=durability)
    indexes = _CountingTuple(("audit:by_sender:sender", "audit:by_target:target"))
    fields = _CountingTuple((("message", 'quoted "☃"'),))
    append = StreamAppend("queue", fields)
    fields.iterations = 0
    writes = [_pending_write(str(index), indexes, append) for index in range(2)]
    try:
        receipts = await audit._commit_batch("audit:messages", writes)
        assert fields.iterations == 2
        assert indexes.iterations == 4
        assert durability.calls == 1
        assert [attempt.previous for attempt in redis.attempts] == [
            "",
            "a" * 64,
            "b" * 64,
        ]
        for attempt in redis.attempts:
            assert attempt.keys == redis.attempts[0].keys
            assert [record["indexes"] for record in attempt.records] == [[3, 4]] * 2
            assert [record["append"] for record in attempt.records] == [
                {"key": 5, "fields": ["message", 'quoted "☃"']}
            ] * 2
        first_fields, first_hash = writes[0].prepared.linked("b" * 64)
        second_fields, final_hash = writes[1].prepared.linked(first_hash)
        assert receipts == [
            (writes[0], AuditWriteReceipt("10-0", "b" * 64)),
            (writes[1], AuditWriteReceipt("10-1", first_hash)),
        ]
        assert redis.attempts[-1].final == final_hash
        for record, expected in zip(
            redis.attempts[-1].records, (first_fields, second_fields), strict=True
        ):
            assert record["fields"] == [
                value for pair in expected.items() for value in pair
            ]
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_capacity_prefix_reprepares_maps_before_cas_retry() -> None:
    redis = _ScriptedRedis([["capacity", "1"], ["retry", "a" * 64], ["ok", "10-0"]])
    durability = _ObservedDurability(redis)
    audit = AuditModule(redis, file_sink=None, durability=durability)
    kept = _pending_write(
        "kept", ("audit:by_sender:a",), StreamAppend("queue-a", (("message", "a"),))
    )
    refused = _pending_write(
        "refused",
        ("audit:by_sender:b",),
        StreamAppend("queue-b", (("message", "b"),)),
    )
    try:
        receipts = await audit._commit_batch("audit:messages", [kept, refused])
        assert len(redis.attempts[0].keys) == 6
        for attempt in redis.attempts[1:]:
            assert attempt.keys == (
                "audit:messages",
                "audit:last_hash",
                "audit:by_sender:a",
                "queue-a",
            )
            assert len(attempt.records) == 1
            assert attempt.records[0]["indexes"] == [3]
            assert attempt.records[0]["append"] == {
                "key": 4,
                "fields": ["message", "a"],
            }
        assert receipts == [(kept, AuditWriteReceipt("10-0", "a" * 64))]
        assert isinstance(refused.result.exception(), AuditCapacityError)
        assert durability.calls == 1
    finally:
        await redis.aclose()

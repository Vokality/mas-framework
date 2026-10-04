"""Audit Module for Gateway Service."""

from __future__ import annotations

import asyncio
import contextlib
import csv
import hashlib
import io
import json
import logging
import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, TypedDict

from mas_core import JsonObject, SpanKind, get_telemetry, validate_json_value
from mas_core.durability import RedisDurability
from mas_core.protocol import validate_json_object
from mas_core.redis_commit import RedisCommitTarget, StreamAppend
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from redis.asyncio import Redis

from .audit_archive import (
    ArchivedAuditRow,
    AuditArchive,
    AuditArchiveError,
    AuditCapacityError,
    AuditCheckpoint,
    AuditRetentionSettings,
    AuditScanLimitError,
    AuditSegment,
    AuditStream,
)

logger = logging.getLogger(__name__)

AuditRecord = JsonObject
_STREAM_ADAPTER = TypeAdapter(list[tuple[str, dict[str, str]]])
_STRING_ADAPTER = TypeAdapter(str)
_OPTIONAL_STRING_ADAPTER = TypeAdapter(str | None)
_HEAD_ADAPTER = TypeAdapter(Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None)
_STRINGS_ADAPTER = TypeAdapter(list[str])
_SNAPSHOT_ADAPTER = TypeAdapter(
    tuple[str | None, str | None, list[tuple[str, dict[str, str]]]]
)
_ARCHIVE_SNAPSHOT_ADAPTER = TypeAdapter(
    tuple[str | None, list[tuple[str, dict[str, str]]]]
)
_STATS_ADAPTER = TypeAdapter(tuple[int, int, str | None, str | None])
_MAX_SCAN_ENTRIES = 1_000_000
_ROUTABLE_DECISIONS = frozenset({"ALLOWED", "ALERT", "DLP_REDACTED"})

_APPEND_SCRIPT = """
for i, key in ipairs(KEYS) do
    local kind = redis.call('TYPE', key).ok
    local expected = i == 2 and 'string' or 'stream'
    if kind ~= 'none' and kind ~= expected then
        return redis.error_reply('invalid_audit_key_type')
    end
end
local previous = redis.call('GET', KEYS[2]) or ''
if previous ~= ARGV[1] then return {'retry', previous} end
local length = redis.call('XLEN', KEYS[1])
if length > 0 and previous == '' then
    return redis.error_reply('missing_audit_chain_head')
end
local records = cjson.decode(ARGV[4])
if type(records) ~= 'table' or #records == 0 then
    return redis.error_reply('invalid_audit_batch')
end
local available = tonumber(ARGV[3]) - length
if #records > available then return {'capacity', tostring(available)} end
local index_count = tonumber(ARGV[5])
if not index_count or index_count < 0 or index_count > #KEYS - 2
    or index_count ~= math.floor(index_count) then
    return redis.error_reply('invalid_audit_indexes')
end
local append_counts = {[1] = #records}
for _, record in ipairs(records) do
    local fields = record.fields
    if type(fields) ~= 'table' or #fields == 0 or #fields > 2048
        or #fields % 2 ~= 0 then
        return redis.error_reply('invalid_audit_fields')
    end
    for _, value in ipairs(fields) do
        if type(value) ~= 'string' then
            return redis.error_reply('invalid_audit_fields')
        end
    end
    if type(record.indexes) ~= 'table' then
        return redis.error_reply('invalid_audit_indexes')
    end
    for _, index in ipairs(record.indexes) do
        if type(index) ~= 'number' or index < 3 or index > 2 + index_count
            or index ~= math.floor(index) then
            return redis.error_reply('invalid_audit_indexes')
        end
    end
    local effect = record.append
    if effect ~= nil and effect ~= cjson.null then
        if type(effect) ~= 'table' or type(effect.key) ~= 'number'
            or effect.key <= 2 + index_count or effect.key > #KEYS
            or effect.key ~= math.floor(effect.key)
            or type(effect.fields) ~= 'table' or #effect.fields == 0
            or #effect.fields > 2048 or #effect.fields % 2 ~= 0 then
            return redis.error_reply('invalid_audit_stream_append')
        end
        for _, value in ipairs(effect.fields) do
            if type(value) ~= 'string' then
                return redis.error_reply('invalid_audit_stream_append')
            end
        end
        append_counts[effect.key] = (append_counts[effect.key] or 0) + 1
    end
end
local function last_id(key)
    if redis.call('EXISTS', key) == 0 then return '0-0' end
    local info = redis.call('XINFO', 'STREAM', key)
    for i = 1, #info, 2 do
        if info[i] == 'last-generated-id' then return info[i + 1] end
    end
end
local function greater(left, right)
    local lm, ls = string.match(left, '^(%d+)%-(%d+)$')
    local rm, rs = string.match(right, '^(%d+)%-(%d+)$')
    if lm ~= rm then return #lm > #rm or (#lm == #rm and lm > rm) end
    return #ls > #rs or (#ls == #rs and ls > rs)
end
local main_last = last_id(KEYS[1])
for i = 3, 2 + index_count do
    if greater(last_id(KEYS[i]), main_last) then
        return redis.error_reply('invalid_audit_index_position')
    end
end
local function increment(value)
    local result = ''
    local carry = 1
    for i = #value, 1, -1 do
        local digit = tonumber(string.sub(value, i, i)) + carry
        carry = digit >= 10 and 1 or 0
        result = tostring(digit % 10) .. result
    end
    return carry == 1 and '1' .. result or result
end
local maximum = '18446744073709551615'
for key, count in pairs(append_counts) do
    local milliseconds, sequence = string.match(last_id(KEYS[key]), '^(%d+)%-(%d+)$')
    if milliseconds == maximum then
        for _ = 1, count do sequence = increment(sequence) end
        if #sequence > #maximum or (#sequence == #maximum and sequence > maximum) then
            return redis.error_reply('audit_stream_id_exhausted')
        end
    end
end
local result = {'ok'}
for _, record in ipairs(records) do
    local id = redis.call('XADD', KEYS[1], '*', unpack(record.fields))
    for _, index in ipairs(record.indexes) do
        redis.call('XADD', KEYS[index], id, unpack(record.fields))
    end
    if record.append ~= nil and record.append ~= cjson.null then
        redis.call('XADD', KEYS[record.append.key], '*', unpack(record.append.fields))
    end
    result[#result + 1] = id
end
redis.call('SET', KEYS[2], ARGV[2])
return result
"""

_ARCHIVE_SCRIPT = """
for i, key in ipairs(KEYS) do
    local kind = redis.call('TYPE', key).ok
    local expected = i == 2 and 'string' or 'stream'
    if kind ~= 'none' and kind ~= expected then
        return redis.error_reply('invalid_audit_key_type')
    end
end
if (redis.call('GET', KEYS[2]) or '') ~= ARGV[1] then return false end
local expected = cjson.decode(ARGV[4])
local current = redis.call('XRANGE', KEYS[1], '-', ARGV[2], 'COUNT', #expected + 1)
if #current ~= #expected then return false end
for i, row in ipairs(current) do
    if row[1] ~= expected[i].stream_id then return false end
    local fields = expected[i].fields
    local field_count = 0
    for _, _ in pairs(fields) do field_count = field_count + 1 end
    if #row[2] ~= field_count * 2 then return false end
    for j = 1, #row[2], 2 do
        if fields[row[2][j]] ~= row[2][j + 1] then return false end
    end
end
redis.call('SET', KEYS[2], ARGV[5])
for i, key in ipairs(KEYS) do
    if i ~= 2 then
        redis.call('XTRIM', key, 'MINID', '=', ARGV[3])
        if i > 2 and redis.call('XLEN', key) == 0 then redis.call('DEL', key) end
    end
end
return true
"""


class AuditStats(TypedDict):
    """Persisted audit counts for health and management views."""

    total_messages: int
    security_events: int


class AuditEntry(BaseModel):
    """Audit log entry."""

    model_config = ConfigDict(extra="forbid")

    message_id: str
    timestamp: float = Field(default_factory=time.time, allow_inf_nan=False)
    sender_id: str
    sender_instance_id: str | None = None
    target_id: str
    message_type: str | None = None
    correlation_id: str | None = None
    decision: str  # ALLOWED, DENIED, RATE_LIMITED, DLP_BLOCKED, etc.
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    payload_hash: str
    violations: list[str] = Field(default_factory=list)
    previous_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    hash_version: int = Field(default=2, ge=1, le=2)


class AuditMessageRecord(AuditEntry):
    """Validated audit message with its persisted stream identity."""

    stream_id: str


class SecurityEvent(BaseModel):
    """Validated security event stored in the audit stream."""

    model_config = ConfigDict(extra="forbid")

    timestamp: float = Field(allow_inf_nan=False)
    event_type: str
    details: JsonObject
    previous_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    hash_version: int = Field(default=2, ge=1, le=2)


@dataclass(frozen=True, slots=True)
class AuditWriteReceipt:
    """The individual identity and chain link of a durably appended record."""

    stream_id: str
    previous_hash: str | None


@dataclass(frozen=True, slots=True)
class PreparedAuditEntry:
    """Cache immutable record serialization while leaving its chain link open."""

    canonical_prefix: str
    canonical_suffix: str
    fields: tuple[tuple[str, str | None], ...]

    @classmethod
    def from_entry(cls, entry: AuditEntry | SecurityEvent) -> PreparedAuditEntry:
        """Validate and serialize static values once before a queued CAS attempt."""
        data = validate_json_object(entry.model_dump(mode="json"))
        keys = sorted(data)
        link_index = keys.index("previous_hash")
        before = {key: data[key] for key in keys[:link_index]}
        after = {key: data[key] for key in keys[link_index + 1 :]}
        return cls(
            json.dumps(before, sort_keys=True, separators=(",", ":"))[:-1]
            + ("," if before else "")
            + '"previous_hash":',
            ("," if after else "")
            + json.dumps(after, sort_keys=True, separators=(",", ":"))[1:],
            tuple(
                (
                    key,
                    None
                    if key == "previous_hash"
                    else json.dumps(value)
                    if isinstance(value, (list, dict))
                    else str(value),
                )
                for key, value in data.items()
                if key == "previous_hash" or value is not None
            ),
        )

    def linked(self, previous_hash: str | None) -> tuple[dict[str, str], str]:
        """Fill the current predecessor without reserializing the static record."""
        fields: dict[str, str] = {}
        for key, value in self.fields:
            linked_value = previous_hash if key == "previous_hash" else value
            if linked_value is not None:
                fields[key] = linked_value
        canonical = (
            self.canonical_prefix + json.dumps(previous_hash) + self.canonical_suffix
        )
        return fields, hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PendingAuditWrite:
    """One queued domain record awaiting its individual commit receipt."""

    entry: AuditEntry | SecurityEvent
    stream: AuditStream
    indexes: tuple[str, ...]
    result: asyncio.Future[AuditWriteReceipt]
    stream_append: StreamAppend | None
    instrumented: bool
    prepared: PreparedAuditEntry


class AuditFileSink:
    """Append-only audit file sink with rotation."""

    def __init__(self, file_path: str, *, max_bytes: int, backup_count: int) -> None:
        self._path = Path(file_path)
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        self._lock = asyncio.Lock()

    @property
    def path(self) -> str:
        return str(self._path)

    async def write_entry(self, entry: AuditEntry) -> None:
        data = entry.model_dump()
        data["violations"] = entry.violations
        line = json.dumps(data, sort_keys=True) + "\n"
        async with self._lock:
            await asyncio.to_thread(self._rotate_and_write, line)

    def _rotate_and_write(self, line: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._should_rotate(line):
            self._rotate_files()
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line)

    def _should_rotate(self, line: str) -> bool:
        if self._max_bytes <= 0:
            return False
        if not self._path.exists():
            return False
        return self._path.stat().st_size + len(line.encode("utf-8")) > self._max_bytes

    def _rotate_files(self) -> None:
        if self._backup_count <= 0:
            with contextlib.suppress(FileNotFoundError):
                self._path.unlink()
            return

        for index in range(self._backup_count - 1, 0, -1):
            src = self._path.with_suffix(self._path.suffix + f".{index}")
            dest = self._path.with_suffix(self._path.suffix + f".{index + 1}")
            if src.exists():
                src.replace(dest)

        rotated = self._path.with_suffix(self._path.suffix + ".1")
        if self._path.exists():
            self._path.replace(rotated)


class AuditModule:
    """
    Audit module for immutable message logging.

    Implements audit logging using Redis Streams as per GATEWAY.md:
    - Immutable append-only logs
    - Complete audit trail
    - Hash chain for tamper detection
    - Queryable by sender, target, time
    - Security event tracking

    Redis Data Model:
        audit:messages → Main audit stream (all messages)
        audit:by_sender:{sender_id} → Indexed by sender
        audit:by_target:{target_id} → Indexed by target
        audit:security_events → Security-specific events
        audit:last_hash → Last hash for chain integrity
    """

    def __init__(
        self,
        redis: Redis,
        *,
        file_sink: AuditFileSink | None,
        retention: AuditRetentionSettings = AuditRetentionSettings(),
        durability: RedisDurability | None = None,
    ) -> None:
        """
        Initialize audit module.

        Args:
            redis: Redis connection
        """
        self.redis = redis
        self._file_sink = file_sink
        self._heads: dict[AuditStream, str | None] = {}
        self._append_locks: dict[AuditStream, asyncio.Lock] = {
            "audit:messages": asyncio.Lock(),
            "audit:security_events": asyncio.Lock(),
        }
        self._retention = retention
        self._durability = durability or RedisDurability()
        self._archive = (
            AuditArchive(retention.archive_directory)
            if retention.archive_directory is not None
            else None
        )
        self._pending: asyncio.Queue[PendingAuditWrite] = asyncio.Queue(
            maxsize=2 * retention.batch_size
        )
        self._pending_results: set[asyncio.Future[AuditWriteReceipt]] = set()
        self._writer: asyncio.Task[None] | None = None
        self._active_batches: set[asyncio.Task[None]] = set()
        self._batch_slots = asyncio.Semaphore(2)
        self._closed = False

    @property
    def commit_target(self) -> RedisCommitTarget:
        """Describe the backend and confirmation policy for combined writes."""
        return RedisCommitTarget(self.redis.connection_pool, self._durability)

    async def log_message(
        self,
        message_id: str,
        sender_id: str,
        target_id: str,
        decision: str,
        latency_ms: float,
        payload: AuditRecord,
        violations: list[str] | None = None,
        *,
        message_type: str | None = None,
        correlation_id: str | None = None,
        sender_instance_id: str | None = None,
        stream_append: StreamAppend | None = None,
    ) -> str:
        """
        Log message to audit stream.

        A compare-and-append operation preserves one chain across all writers.
        Full streams are archived durably or refuse new writes without discard.

        Args:
            message_id: Unique message identifier
            sender_id: Sender agent ID
            target_id: Target agent ID
            decision: Gateway decision (ALLOWED, DENIED, etc.)
            latency_ms: Processing latency in milliseconds
            payload: Message payload (will be hashed)
            violations: List of policy violations

        Returns:
            Stream entry ID
        """
        telemetry = get_telemetry()
        if stream_append is not None and (
            decision not in _ROUTABLE_DECISIONS
            or stream_append.stream.startswith("audit:")
        ):
            raise ValueError(
                "stream append requires a routable decision and non-audit stream"
            )
        with telemetry.start_span(
            "mas.gateway.audit.log_message",
            kind=SpanKind.INTERNAL,
            attributes={"mas.decision": decision},
        ):
            payload_str = json.dumps(payload, sort_keys=True)
            entry = AuditEntry(
                message_id=message_id,
                sender_id=sender_id,
                sender_instance_id=sender_instance_id,
                target_id=target_id,
                message_type=message_type,
                correlation_id=correlation_id,
                decision=decision,
                latency_ms=latency_ms,
                payload_hash=hashlib.sha256(payload_str.encode()).hexdigest(),
                violations=violations or [],
            )
            main_stream_id, entry = await self._append_entry(
                entry,
                "audit:messages",
                (f"audit:by_sender:{sender_id}", f"audit:by_target:{target_id}"),
                stream_append,
            )

            logger.debug(
                "Audit entry logged",
                extra={
                    "message_id": message_id,
                    "decision": decision,
                    "stream_id": main_stream_id,
                },
            )

            if self._file_sink is not None:
                try:
                    await self._file_sink.write_entry(entry)
                except Exception as exc:
                    logger.error(
                        "Failed to write audit file",
                        exc_info=exc,
                        extra={"path": self._file_sink.path},
                    )

            return main_stream_id

    async def close(self) -> None:
        """Finish audit work before the owning Redis connection is closed."""
        self._closed = True
        while self._pending_results:
            await asyncio.shield(
                asyncio.gather(*self._pending_results, return_exceptions=True)
            )
        if self._writer is not None:
            await asyncio.shield(self._writer)
        if self._active_batches:
            await asyncio.shield(
                asyncio.gather(*self._active_batches, return_exceptions=True)
            )

    async def log_security_event(
        self,
        event_type: str,
        details: AuditRecord,
        *,
        instrumented: bool = True,
    ) -> str:
        """
        Log security event to audit stream.

        Args:
            event_type: Event type (AUTH_FAILURE, AUTHZ_DENIED, etc.)
            details: Event details dictionary
            instrumented: Emit operational spans for this audit write. Collectors
                disable this to avoid exporting observations of their own intake.

        Returns:
            Stream entry ID
        """
        stream_id, _ = await self._append_entry(
            SecurityEvent(
                timestamp=time.time(), event_type=event_type, details=details
            ),
            "audit:security_events",
            instrumented=instrumented,
        )

        logger.info(
            "Security event logged",
            extra={"event_type": event_type, "stream_id": stream_id},
        )

        return _STRING_ADAPTER.validate_python(stream_id)

    async def _append_entry[EntryT: AuditEntry | SecurityEvent](
        self,
        entry: EntryT,
        stream: AuditStream,
        indexes: tuple[str, ...] = (),
        stream_append: StreamAppend | None = None,
        *,
        instrumented: bool = True,
    ) -> tuple[str, EntryT]:
        if self._closed:
            raise AuditArchiveError("audit_writer_closed")
        result: asyncio.Future[AuditWriteReceipt] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending_results.add(result)
        result.add_done_callback(self._result_finished)
        try:
            await self._pending.put(
                PendingAuditWrite(
                    entry,
                    stream,
                    indexes,
                    result,
                    stream_append,
                    instrumented,
                    PreparedAuditEntry.from_entry(entry),
                )
            )
        except BaseException:
            result.cancel()
            raise
        if self._writer is None or self._writer.done():
            self._writer = asyncio.create_task(self._run_writer())
        receipt = await asyncio.shield(result)
        return receipt.stream_id, entry.model_copy(
            update={"previous_hash": receipt.previous_hash}
        )

    def _result_finished(self, result: asyncio.Future[AuditWriteReceipt]) -> None:
        self._pending_results.discard(result)
        if not result.cancelled():
            result.exception()

    async def _run_writer(self) -> None:
        while not self._pending.empty():
            await self._batch_slots.acquire()
            await asyncio.sleep(0.001)
            groups: dict[AuditStream, list[PendingAuditWrite]] = {}
            for _ in range(self._retention.batch_size):
                try:
                    write = self._pending.get_nowait()
                except asyncio.QueueEmpty:
                    break
                groups.setdefault(write.stream, []).append(write)
            reserved = True
            for stream, writes in groups.items():
                capacity = (
                    self._retention.max_messages
                    if stream == "audit:messages"
                    else self._retention.max_security_events
                )
                for offset in range(0, len(writes), capacity):
                    if not reserved:
                        await self._batch_slots.acquire()
                    reserved = False
                    task = asyncio.create_task(
                        self._complete_batch(stream, writes[offset : offset + capacity])
                    )
                    self._active_batches.add(task)
                    task.add_done_callback(self._active_batches.discard)
            if reserved:
                self._batch_slots.release()

    async def _complete_batch(
        self, stream: AuditStream, writes: list[PendingAuditWrite]
    ) -> None:
        try:
            with (
                get_telemetry().start_span(
                    "mas.gateway.audit.commit_batch",
                    attributes={
                        "mas.audit.stream": stream,
                        "mas.audit.batch_size": len(writes),
                    },
                )
                if any(write.instrumented for write in writes)
                else contextlib.nullcontext()
            ):
                receipts = await self._commit_batch(stream, writes)
            for write, receipt in receipts:
                if not write.result.done():
                    write.result.set_result(receipt)
        except BaseException as exc:
            for write in writes:
                if not write.result.done():
                    write.result.set_exception(exc)
        finally:
            self._batch_slots.release()

    async def _commit_batch(
        self, stream: AuditStream, writes: list[PendingAuditWrite]
    ) -> list[tuple[PendingAuditWrite, AuditWriteReceipt]]:
        head_key = self._head_key(stream)
        capacity = (
            self._retention.max_messages
            if stream == "audit:messages"
            else self._retention.max_security_events
        )
        active = writes
        while True:
            indexes = sorted({index for write in active for index in write.indexes})
            effects = sorted(
                {
                    write.stream_append.stream
                    for write in active
                    if write.stream_append is not None
                }
            )
            positions = {key: i + 3 for i, key in enumerate([*indexes, *effects])}
            records: list[dict[str, object]] = [
                {
                    "fields": [],
                    "indexes": [positions[index] for index in write.indexes],
                    "append": (
                        {
                            "key": positions[write.stream_append.stream],
                            "fields": [
                                value
                                for pair in write.stream_append.fields
                                for value in pair
                            ],
                        }
                        if write.stream_append is not None
                        else None
                    ),
                }
                for write in active
            ]
            while True:
                async with self.redis.client() as connection:
                    async with self._append_locks[stream]:
                        previous = self._heads.get(stream)
                        links: list[str | None] = []
                        final_hash = previous
                        for write, record in zip(active, records, strict=True):
                            links.append(final_hash)
                            fields, final_hash = write.prepared.linked(final_hash)
                            record["fields"] = [
                                value for pair in fields.items() for value in pair
                            ]
                        with (
                            get_telemetry().start_span(
                                "mas.gateway.audit.append_batch",
                                attributes={"mas.audit.batch_size": len(active)},
                            )
                            if any(write.instrumented for write in active)
                            else contextlib.nullcontext()
                        ):
                            result = _STRINGS_ADAPTER.validate_python(
                                await connection.eval(
                                    _APPEND_SCRIPT,
                                    2 + len(indexes) + len(effects),
                                    stream,
                                    head_key,
                                    *indexes,
                                    *effects,
                                    previous or "",
                                    final_hash or "",
                                    capacity,
                                    json.dumps(records),
                                    len(indexes),
                                )
                            )
                        if len(result) == 2 and result[0] == "retry":
                            try:
                                self._heads[stream] = _HEAD_ADAPTER.validate_python(
                                    result[1] or None
                                )
                            except ValidationError as exc:
                                raise AuditArchiveError(
                                    "invalid_audit_chain_head"
                                ) from exc
                            continue
                        at_capacity = len(result) == 2 and result[0] == "capacity"
                        if not at_capacity:
                            if len(result) != len(active) + 1 or result[0] != "ok":
                                raise AuditArchiveError("invalid_audit_append_result")
                            self._heads[stream] = final_hash
                    if at_capacity:
                        if self._archive is None:
                            available = int(result[1])
                            if available <= 0:
                                raise AuditCapacityError(
                                    f"audit_capacity_exceeded:{stream}"
                                )
                            if available >= len(active):
                                raise AuditArchiveError("invalid_audit_capacity_result")
                            for write in active[available:]:
                                write.result.set_exception(
                                    AuditCapacityError(
                                        f"audit_capacity_exceeded:{stream}"
                                    )
                                )
                            active = active[:available]
                            break
                        await self._archive_prefix(stream)
                        continue
                    with (
                        get_telemetry().start_span("mas.gateway.audit.confirm_batch")
                        if any(write.instrumented for write in active)
                        else contextlib.nullcontext()
                    ):
                        await self._durability.confirm(connection)
                    return [
                        (write, AuditWriteReceipt(id_, link))
                        for write, id_, link in zip(
                            active, result[1:], links, strict=True
                        )
                    ]

    @staticmethod
    def _head_key(stream: AuditStream) -> str:
        return (
            "audit:last_hash"
            if stream == "audit:messages"
            else "audit:security_last_hash"
        )

    async def _archive_prefix(self, stream: AuditStream) -> None:
        archive = self._archive
        if archive is None:
            raise AuditCapacityError(f"audit_capacity_exceeded:{stream}")
        checkpoint_key = f"{stream}:checkpoint"
        async with self.redis.pipeline(transaction=True) as pipeline:
            pipeline.get(checkpoint_key)
            pipeline.xrange(stream, count=self._retention.batch_size)
            old_value, rows = _ARCHIVE_SNAPSHOT_ADAPTER.validate_python(
                await pipeline.execute()
            )
        previous = (
            AuditCheckpoint.model_validate_json(old_value)
            if old_value is not None
            else None
        )
        if not rows:
            return
        expected_hash = previous.last_hash if previous is not None else None
        indexes: set[str] = set()
        for _, raw in rows:
            entry = self._chain_entry(stream, raw)
            if entry.hash_version != 2 or entry.previous_hash != expected_hash:
                raise AuditArchiveError("audit_archive_chain_invalid")
            expected_hash = self._hash_entry(entry)
            if isinstance(entry, AuditEntry):
                indexes.update(
                    (
                        f"audit:by_sender:{entry.sender_id}",
                        f"audit:by_target:{entry.target_id}",
                    )
                )
        assert expected_hash is not None
        segment = AuditSegment(
            stream=stream,
            previous_checkpoint=previous,
            rows=[ArchivedAuditRow(stream_id=id_, fields=raw) for id_, raw in rows],
            last_hash=expected_hash,
        )
        digest = await archive.write(segment)
        last_id = rows[-1][0]
        milliseconds, sequence = last_id.split("-")
        if int(sequence) < 2**64 - 1:
            next_id = f"{milliseconds}-{int(sequence) + 1}"
        elif int(milliseconds) < 2**64 - 1:
            next_id = f"{int(milliseconds) + 1}-0"
        else:
            raise AuditArchiveError("audit_stream_identity_exhausted")
        checkpoint = AuditCheckpoint(
            stream=stream,
            last_stream_id=last_id,
            last_hash=expected_hash,
            segment_digest=digest,
            total_entries=(previous.total_entries if previous is not None else 0)
            + len(rows),
        )
        async with self.redis.client() as connection:
            committed = await connection.eval(
                _ARCHIVE_SCRIPT,
                2 + len(indexes),
                stream,
                checkpoint_key,
                *sorted(indexes),
                old_value or "",
                last_id,
                next_id,
                json.dumps([row.model_dump() for row in segment.rows]),
                checkpoint.model_dump_json(),
            )
            if committed:
                await self._durability.confirm(connection)

    @staticmethod
    def _chain_entry(
        stream: AuditStream, raw: dict[str, str]
    ) -> AuditEntry | SecurityEvent:
        if stream == "audit:messages":
            message = AuditModule._record_to_entry(raw)
            if message is None:
                raise AuditArchiveError("invalid_audit_message")
            return message
        try:
            fields: dict[str, object] = dict(raw)
            fields["details"] = validate_json_value(json.loads(raw["details"]))
            fields.setdefault("hash_version", 1)
            return SecurityEvent.model_validate(fields)
        except (KeyError, ValueError) as exc:
            raise AuditArchiveError("invalid_audit_security_event") from exc

    async def query_by_sender(
        self,
        sender_id: str,
        start_time: float | None = None,
        end_time: float | None = None,
        count: int = 100,
    ) -> list[AuditRecord]:
        """
        Query audit log by sender.

        Args:
            sender_id: Sender agent ID
            start_time: Start timestamp (None = beginning)
            end_time: End timestamp (None = now)
            count: Maximum number of entries to return

        Returns:
            List of audit entries
        """
        return await self._query_indexed_stream(
            "audit:by_sender", sender_id, start_time, end_time, count
        )

    async def query_by_target(
        self,
        target_id: str,
        start_time: float | None = None,
        end_time: float | None = None,
        count: int = 100,
    ) -> list[AuditRecord]:
        """
        Query audit log by target.

        Args:
            target_id: Target agent ID
            start_time: Start timestamp (None = beginning)
            end_time: End timestamp (None = now)
            count: Maximum number of entries to return

        Returns:
            List of audit entries
        """
        return await self._query_indexed_stream(
            "audit:by_target", target_id, start_time, end_time, count
        )

    async def query_security_events(
        self,
        start_time: float | None = None,
        end_time: float | None = None,
        count: int = 100,
    ) -> list[AuditRecord]:
        """
        Query security events.

        Args:
            start_time: Start timestamp (None = beginning)
            end_time: End timestamp (None = now)
            count: Maximum number of entries to return

        Returns:
            List of security events
        """
        return await self._query_stream(
            "audit:security_events", start_time, end_time, count
        )

    async def _query_indexed_stream(
        self,
        prefix: str,
        key: str,
        start_time: float | None,
        end_time: float | None,
        count: int,
    ) -> list[AuditRecord]:
        """Query an index stream keyed by sender/target."""
        stream = f"{prefix}:{key}"
        return await self._query_stream(stream, start_time, end_time, count)

    async def _query_messages(
        self,
        start_time: float | None,
        end_time: float | None,
        count: int | None,
    ) -> list[AuditRecord]:
        """Query the main audit message stream."""
        return await self._query_stream("audit:messages", start_time, end_time, count)

    async def _query_stream(
        self,
        stream: str,
        start_time: float | None,
        end_time: float | None,
        count: int | None,
        predicate: Callable[[AuditRecord], bool] | None = None,
    ) -> list[AuditRecord]:
        """Page through a time range, stopping after the requested matches."""
        if count is not None and count < 0:
            raise ValueError("count must be nonnegative")
        for timestamp in (start_time, end_time):
            if timestamp is not None and (
                not math.isfinite(timestamp) or timestamp < 0
            ):
                raise ValueError("Audit timestamps must be finite and nonnegative")
        if start_time is not None and end_time is not None and start_time > end_time:
            raise ValueError("start_time must not exceed end_time")
        if count == 0:
            return []
        start_id = (
            self._timestamp_to_stream_id(start_time) if start_time is not None else "-"
        )
        end_id = str(int(end_time * 1000)) if end_time is not None else "+"
        result, checkpoint_value = await self._query_archived(
            stream, start_time, end_time, count, predicate
        )
        scanned = (
            AuditCheckpoint.model_validate_json(checkpoint_value).total_entries
            if checkpoint_value is not None
            else 0
        )
        if count is not None and len(result) >= count:
            return result
        cursor = start_id
        archive_stream: AuditStream = (
            "audit:security_events"
            if stream == "audit:security_events"
            else "audit:messages"
        )

        async def finish() -> list[AuditRecord]:
            current = _OPTIONAL_STRING_ADAPTER.validate_python(
                await self.redis.get(f"{archive_stream}:checkpoint")
            )
            if current != checkpoint_value:
                raise AuditArchiveError("audit_query_snapshot_changed_retry")
            return result

        telemetry = get_telemetry()
        with telemetry.start_span(
            "mas.gateway.audit.query_stream",
            kind=SpanKind.INTERNAL,
            attributes={"mas.audit.stream": stream},
        ):
            try:
                while True:
                    batch_size = (
                        min(500, count - len(result))
                        if count is not None and predicate is None
                        else 500
                    )
                    batch_size = min(batch_size, _MAX_SCAN_ENTRIES - scanned + 1)
                    entries = _STREAM_ADAPTER.validate_python(
                        await self.redis.xrange(
                            stream, cursor, end_id, count=batch_size
                        )
                    )
                    if not entries:
                        return await finish()
                    scanned += len(entries)
                    if scanned > _MAX_SCAN_ENTRIES:
                        raise AuditScanLimitError("audit_query_scan_limit_exceeded")
                    for stream_id, raw in entries:
                        record = self._stream_entry_to_record(stream_id, raw)
                        if predicate is None or predicate(record):
                            result.append(record)
                            if count is not None and len(result) >= count:
                                return await finish()
                    if len(entries) < batch_size:
                        return await finish()
                    cursor = f"({entries[-1][0]}"
            except Exception:
                telemetry.record_redis_error(component="audit", operation="xrange")
                raise

    async def _query_archived(
        self,
        stream: str,
        start_time: float | None,
        end_time: float | None,
        count: int | None,
        predicate: Callable[[AuditRecord], bool] | None,
    ) -> tuple[list[AuditRecord], str | None]:
        archive_stream: AuditStream = (
            "audit:security_events"
            if stream == "audit:security_events"
            else "audit:messages"
        )
        raw_checkpoint = _OPTIONAL_STRING_ADAPTER.validate_python(
            await self.redis.get(f"{archive_stream}:checkpoint")
        )
        if raw_checkpoint is None:
            return [], None
        if self._archive is None:
            raise AuditArchiveError("audit_archive_directory_required")
        checkpoint = AuditCheckpoint.model_validate_json(raw_checkpoint)
        if checkpoint.total_entries > _MAX_SCAN_ENTRIES:
            raise AuditScanLimitError("audit_query_scan_limit_exceeded")
        records: deque[AuditRecord] = deque(maxlen=count)
        lower = (int(start_time * 1000), 0) if start_time is not None else None
        upper = (int(end_time * 1000), 2**64 - 1) if end_time is not None else None
        while checkpoint is not None:
            segment = await self._archive.read(checkpoint.segment_digest)
            if segment.stream != archive_stream or checkpoint.stream != archive_stream:
                raise AuditArchiveError("audit_archive_stream_mismatch")
            predecessor = segment.previous_checkpoint
            if predecessor is not None and (
                predecessor.total_entries >= checkpoint.total_entries
                or self._stream_id(predecessor.last_stream_id)
                >= self._stream_id(segment.rows[0].stream_id)
            ):
                raise AuditArchiveError("audit_archive_checkpoint_invalid")
            for row in reversed(segment.rows):
                identity = self._stream_id(row.stream_id)
                if (lower is not None and identity < lower) or (
                    upper is not None and identity > upper
                ):
                    continue
                record = self._stream_entry_to_record(row.stream_id, row.fields)
                if stream.startswith("audit:by_sender:") and record.get(
                    "sender_id"
                ) != stream.removeprefix("audit:by_sender:"):
                    continue
                if stream.startswith("audit:by_target:") and record.get(
                    "target_id"
                ) != stream.removeprefix("audit:by_target:"):
                    continue
                if predicate is None or predicate(record):
                    records.appendleft(record)
            checkpoint = predecessor
        return list(records), raw_checkpoint

    async def query_recent(self, count: int = 100) -> list[AuditMessageRecord]:
        """Read the latest validated messages in descending stream order."""
        if count < 0:
            raise ValueError("count must be nonnegative")
        if count == 0:
            return []
        entries = _STREAM_ADAPTER.validate_python(
            await self.redis.xrevrange("audit:messages", count=count)
        )
        return [
            AuditMessageRecord.model_validate(
                self._stream_entry_to_record(stream_id, raw)
            )
            for stream_id, raw in entries
        ]

    async def verify_integrity(
        self, message_id: str, *, max_entries: int = _MAX_SCAN_ENTRIES
    ) -> bool:
        """Verify v2 history in bounded pages through a consistent upper snapshot."""
        return await self._verify_chain("audit:messages", message_id, max_entries)

    async def verify_security_integrity(
        self, *, max_entries: int = _MAX_SCAN_ENTRIES
    ) -> bool:
        """Verify the independent v2 security-event chain and its archives."""
        return await self._verify_chain("audit:security_events", None, max_entries)

    async def _verify_chain(
        self, stream: AuditStream, message_id: str | None, max_entries: int
    ) -> bool:
        if isinstance(max_entries, bool) or max_entries <= 0:
            raise ValueError("max_entries must be positive")
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.get(f"{stream}:checkpoint")
            pipe.get(self._head_key(stream))
            pipe.xrevrange(stream, count=1)
            checkpoint_value, head, tail = _SNAPSHOT_ADAPTER.validate_python(
                await pipe.execute()
            )
        try:
            checkpoint = (
                AuditCheckpoint.model_validate_json(checkpoint_value)
                if checkpoint_value is not None
                else None
            )
            if head is None or (not tail and checkpoint is None):
                return False
            expected = checkpoint.last_hash if checkpoint is not None else None
            last_id = checkpoint.last_stream_id if checkpoint is not None else "0-0"
            found = message_id is None
            scanned = 0
            archived = checkpoint
            while archived is not None:
                if self._archive is None:
                    return False
                segment = await self._archive.read(archived.segment_digest)
                predecessor = segment.previous_checkpoint
                if (
                    segment.stream != stream
                    or archived.stream != stream
                    or segment.last_hash != archived.last_hash
                    or segment.rows[-1].stream_id != archived.last_stream_id
                    or archived.total_entries
                    != (predecessor.total_entries if predecessor is not None else 0)
                    + len(segment.rows)
                ):
                    return False
                archived_hash = predecessor.last_hash if predecessor else None
                archived_id = predecessor.last_stream_id if predecessor else "0-0"
                for row in segment.rows:
                    scanned += 1
                    if scanned > max_entries:
                        raise AuditScanLimitError("audit_integrity_scan_limit_exceeded")
                    entry = self._chain_entry(stream, row.fields)
                    if (
                        entry.hash_version != 2
                        or entry.previous_hash != archived_hash
                        or self._stream_id(row.stream_id)
                        <= self._stream_id(archived_id)
                    ):
                        return False
                    archived_hash = self._hash_entry(entry)
                    archived_id = row.stream_id
                    if isinstance(entry, AuditEntry) and entry.message_id == message_id:
                        found = True
                if archived_hash != archived.last_hash:
                    return False
                archived = predecessor
            upper_id = tail[0][0] if tail else last_id
            cursor = f"({last_id}" if checkpoint is not None else "-"
            while True:
                rows = _STREAM_ADAPTER.validate_python(
                    await self.redis.xrange(
                        stream,
                        cursor,
                        upper_id,
                        count=min(
                            self._retention.batch_size, max_entries - scanned + 1
                        ),
                    )
                )
                if not rows:
                    break
                for stream_id, fields in rows:
                    scanned += 1
                    if scanned > max_entries:
                        raise AuditScanLimitError("audit_integrity_scan_limit_exceeded")
                    entry = self._chain_entry(stream, fields)
                    if entry.hash_version != 2 or entry.previous_hash != expected:
                        return False
                    expected = self._hash_entry(entry)
                    last_id = stream_id
                    if isinstance(entry, AuditEntry) and entry.message_id == message_id:
                        found = True
                cursor = f"({last_id}"
            return found and expected == head and last_id == upper_id
        except (ValueError, KeyError, AuditArchiveError):
            return False

    @staticmethod
    def _stream_id(value: str) -> tuple[int, int]:
        milliseconds, sequence = value.split("-")
        return int(milliseconds), int(sequence)

    @staticmethod
    def _timestamp_to_stream_id(timestamp: float) -> str:
        """
        Convert Unix timestamp to Redis Stream ID.

        Args:
            timestamp: Unix timestamp

        Returns:
            Stream ID in format "timestamp_ms-0"
        """
        timestamp_ms = int(timestamp * 1000)
        return f"{timestamp_ms}-0"

    async def query_by_decision(
        self,
        decision: str,
        start_time: float | None = None,
        end_time: float | None = None,
        count: int = 100,
    ) -> list[AuditRecord]:
        """
        Query audit log by decision type.

        Args:
            decision: Decision type (ALLOWED, DENIED, RATE_LIMITED, DLP_BLOCKED, etc.)
            start_time: Start timestamp (None = beginning)
            end_time: End timestamp (None = now)
            count: Maximum number of entries to return

        Returns:
            List of audit entries matching decision
        """
        return await self._query_stream(
            "audit:messages",
            start_time,
            end_time,
            count,
            predicate=lambda entry: entry.get("decision") == decision,
        )

    async def query_by_violation(
        self,
        violation_type: str,
        start_time: float | None = None,
        end_time: float | None = None,
        count: int = 100,
    ) -> list[AuditRecord]:
        """
        Query audit log by violation type.

        Args:
            violation_type: Violation type (e.g., "PII", "PHI", "PCI", etc.)
            start_time: Start timestamp (None = beginning)
            end_time: End timestamp (None = now)
            count: Maximum number of entries to return

        Returns:
            List of audit entries with specified violation
        """

        def matches(entry: AuditRecord) -> bool:
            violations = entry.get("violations")
            return isinstance(violations, list) and violation_type in violations

        return await self._query_stream(
            "audit:messages", start_time, end_time, count, predicate=matches
        )

    async def query_all(
        self,
        start_time: float | None = None,
        end_time: float | None = None,
        count: int = 100,
    ) -> list[AuditRecord]:
        """
        Query all audit log entries.

        Args:
            start_time: Start timestamp (None = beginning)
            end_time: End timestamp (None = now)
            count: Maximum number of entries to return

        Returns:
            List of all audit entries
        """
        return await self._query_messages(start_time, end_time, count)

    async def export_to_csv(
        self,
        entries: list[AuditRecord],
    ) -> str:
        """
        Export audit entries to CSV format.

        Args:
            entries: List of audit entries to export

        Returns:
            CSV string
        """
        if not entries:
            return ""

        output = io.StringIO()

        # Define CSV columns
        fieldnames = [
            "stream_id",
            "message_id",
            "timestamp",
            "sender_id",
            "sender_instance_id",
            "target_id",
            "message_type",
            "correlation_id",
            "decision",
            "latency_ms",
            "payload_hash",
            "violations",
        ]

        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()

        for entry in entries:
            # Convert violations list to string
            entry_copy = entry.copy()
            violations = entry_copy.get("violations")
            if isinstance(violations, list):
                entry_copy["violations"] = ";".join(
                    item for item in violations if isinstance(item, str)
                )
            writer.writerow(entry_copy)

        return output.getvalue()

    async def export_to_json(
        self,
        entries: list[AuditRecord],
        pretty: bool = True,
    ) -> str:
        """
        Export audit entries to JSON format.

        Args:
            entries: List of audit entries to export
            pretty: Pretty print JSON (default: True)

        Returns:
            JSON string
        """
        if pretty:
            return json.dumps(entries, indent=2, sort_keys=True)
        return json.dumps(entries)

    async def export_compliance_report(
        self,
        start_time: float,
        end_time: float,
        format_type: str = "csv",
    ) -> str:
        """
        Export compliance report for specified time range.

        Args:
            start_time: Start timestamp
            end_time: End timestamp
            format_type: Export format ("csv" or "json")

        Returns:
            Formatted report string
        """
        if format_type not in {"csv", "json"}:
            raise ValueError(f"Unsupported format: {format_type}")
        entries = await self._query_messages(start_time, end_time, count=None)

        if format_type == "csv":
            return await self.export_to_csv(entries)
        elif format_type == "json":
            return await self.export_to_json(entries)
        else:
            raise ValueError(f"Unsupported format: {format_type}")

    async def get_stats(self) -> AuditStats:
        """Read persisted counts; backend failures must not appear as zero activity."""
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.xlen("audit:messages")
            pipe.xlen("audit:security_events")
            pipe.get("audit:messages:checkpoint")
            pipe.get("audit:security_events:checkpoint")
            messages, security, message_anchor, security_anchor = (
                _STATS_ADAPTER.validate_python(await pipe.execute())
            )
        return {
            "total_messages": messages
            + (
                AuditCheckpoint.model_validate_json(message_anchor).total_entries
                if message_anchor is not None
                else 0
            ),
            "security_events": security
            + (
                AuditCheckpoint.model_validate_json(security_anchor).total_entries
                if security_anchor is not None
                else 0
            ),
        }

    @staticmethod
    def _entry_to_stream_fields(
        entry: AuditEntry | SecurityEvent,
    ) -> dict[str, str]:
        """Serialize an audit entry to Redis Stream fields."""
        data = validate_json_value(entry.model_dump(mode="json", exclude_none=True))
        if not isinstance(data, dict):
            raise ValueError("Audit fields must be a JSON object")
        return {
            key: json.dumps(value) if isinstance(value, (list, dict)) else str(value)
            for key, value in data.items()
        }

    @staticmethod
    def _hash_entry(entry: AuditEntry | SecurityEvent) -> str:
        """Hash the canonical record including the link to its predecessor."""
        entry_data = json.dumps(
            entry.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(entry_data.encode()).hexdigest()

    @staticmethod
    def _stream_entry_to_record(stream_id: str, raw: dict[str, str]) -> AuditRecord:
        """Validate persisted fields and decode JSON before returning API data."""
        if "event_type" in raw:
            fields: dict[str, object] = dict(raw)
            fields["details"] = validate_json_value(json.loads(raw["details"]))
            fields.setdefault("hash_version", 1)
            model = SecurityEvent.model_validate(fields)
            data = validate_json_value(model.model_dump(mode="json"))
        else:
            entry = AuditModule._record_to_entry(raw)
            if entry is None:
                raise ValueError(f"Malformed audit message at {stream_id}")
            data = validate_json_value(entry.model_dump(mode="json", exclude_none=True))
        if not isinstance(data, dict):
            raise ValueError("Audit record must be a JSON object")
        data["stream_id"] = stream_id
        return data

    @staticmethod
    def _record_to_entry(raw: dict[str, str]) -> AuditEntry | None:
        """Validate stored stream fields before domain or integrity checks."""
        if "timestamp" not in raw:
            return None
        fields: dict[str, object] = dict(raw)
        fields.setdefault("hash_version", 1)
        try:
            violations: object = json.loads(raw.get("violations", "[]"))
            if not isinstance(violations, list) or not all(
                isinstance(item, str) for item in violations
            ):
                return None
            fields["violations"] = violations
            return AuditEntry.model_validate(fields)
        except (json.JSONDecodeError, ValidationError):
            return None

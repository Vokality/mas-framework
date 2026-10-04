"""Regression evidence for audit integrity, finite capacity and durable archival."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from mas_core.durability import RedisDurabilitySettings
from mas_core.redis_client import SentinelSettings
from mas_gateway.audit import AuditEntry, AuditModule
from mas_gateway.audit_archive import (
    AuditArchiveError,
    AuditCapacityError,
    AuditCheckpoint,
    AuditRetentionSettings,
    AuditScanLimitError,
)
from mas_gateway.config import AuditSettings, GatewaySettings, RedisSettings
from pydantic import TypeAdapter
from redis.asyncio import Redis
from redis.asyncio.client import Pipeline
from redis.exceptions import ResponseError
from redis.typing import KeyT

from integration_tests.production_support import RedisNode

pytestmark = pytest.mark.asyncio
_ROWS = TypeAdapter(list[tuple[str, dict[str, str]]])
_RESPONSES = TypeAdapter(list[object])
_OPTIONAL_STRING = TypeAdapter(str | None)


def _module(redis: Redis, directory: Path | None = None) -> AuditModule:
    return AuditModule(
        redis,
        file_sink=None,
        retention=AuditRetentionSettings(
            max_messages=2,
            max_security_events=2,
            batch_size=1,
            archive_directory=str(directory) if directory is not None else None,
        ),
    )


async def _log(audit: AuditModule, index: int) -> str:
    return await audit.log_message(
        f"message-{index}",
        "sender" if index % 2 == 0 else "other",
        "target",
        "ALLOWED",
        1.25,
        {"index": index},
        violations=["finding"] if index == 0 else [],
    )


async def test_capacity_rejects_new_writes_without_discard(redis: Redis) -> None:
    audit = _module(redis)
    first = await _log(audit, 0)
    second = await _log(audit, 1)
    with pytest.raises(AuditCapacityError, match="audit_capacity_exceeded"):
        await _log(audit, 2)
    assert [
        row[0] for row in _ROWS.validate_python(await redis.xrange("audit:messages"))
    ] == [first, second]
    assert (
        _ROWS.validate_python(await redis.xrange("audit:by_sender:sender"))[0][0]
        == first
    )
    assert (
        _ROWS.validate_python(await redis.xrange("audit:by_target:target"))[1][0]
        == second
    )
    assert await audit.verify_integrity("message-0")
    await audit.close()


async def test_security_capacity_and_chain_are_independent(redis: Redis) -> None:
    audit = _module(redis)
    await audit.log_security_event("denied", {"subject": "operator"})
    await audit.log_security_event("login", {"subject": "operator"})
    with pytest.raises(AuditCapacityError):
        await audit.log_security_event("denied", {})
    assert await _log(audit, 0)
    assert await audit.verify_security_integrity()
    assert await audit.get_stats() == {"total_messages": 1, "security_events": 2}
    await audit.close()


async def test_archival_preserves_queries_counts_indexes_and_integrity(
    redis: Redis, tmp_path: Path
) -> None:
    audit = _module(redis, tmp_path / "archive")
    for index in range(7):
        await _log(audit, index)
        await audit.log_security_event("event", {"index": index})
    assert await redis.xlen("audit:messages") == 2
    assert await redis.xlen("audit:security_events") == 2
    assert await redis.xlen("audit:by_target:target") == 2
    assert await redis.xlen("audit:by_sender:sender") == 1
    assert await redis.xlen("audit:by_sender:other") == 1
    assert await audit.get_stats() == {"total_messages": 7, "security_events": 7}
    assert [row["message_id"] for row in await audit.query_all(count=20)] == [
        f"message-{i}" for i in range(7)
    ]
    assert [row["message_id"] for row in await audit.query_by_sender("sender")] == [
        "message-0",
        "message-2",
        "message-4",
        "message-6",
    ]
    assert [row["message_id"] for row in await audit.query_by_violation("finding")] == [
        "message-0"
    ]
    assert len(await audit.query_security_events()) == 7
    assert await audit.verify_integrity("message-0")
    assert await audit.verify_integrity("message-6")
    assert await audit.verify_security_integrity()
    assert len(json.loads(await audit.export_compliance_report(0, 10**10, "json"))) == 7
    await audit.close()


async def test_archive_io_failure_preserves_live_history(
    redis: Redis, tmp_path: Path
) -> None:
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    audit = _module(redis, blocked)
    await _log(audit, 0)
    await _log(audit, 1)
    head = await redis.get("audit:last_hash")
    with pytest.raises(AuditArchiveError, match="audit_archive_write_failed"):
        await _log(audit, 2)
    assert await redis.xlen("audit:messages") == 2
    assert await redis.xlen("audit:by_target:target") == 2
    assert await redis.get("audit:last_hash") == head
    assert await redis.get("audit:messages:checkpoint") is None
    assert await audit.verify_integrity("message-1")
    await audit.close()


async def test_missing_archive_is_visible_and_never_verified(
    redis: Redis, tmp_path: Path
) -> None:
    directory = tmp_path / "archive"
    audit = _module(redis, directory)
    for index in range(3):
        await _log(audit, index)
    raw = await redis.get("audit:messages:checkpoint")
    assert raw is not None
    checkpoint = AuditCheckpoint.model_validate_json(raw)
    (directory / f"{checkpoint.segment_digest}.json").unlink()
    assert await audit.verify_integrity("message-2") is False
    with pytest.raises(AuditArchiveError):
        await audit.query_all()
    await audit.close()


async def test_middle_delete_and_relink_is_detected(redis: Redis) -> None:
    audit = AuditModule(redis, file_sink=None)
    for index in range(3):
        await _log(audit, index)
    rows = _ROWS.validate_python(await redis.xrange("audit:messages"))
    first = AuditModule._record_to_entry(rows[0][1])
    assert first is not None
    await redis.xdel("audit:messages", rows[1][0], rows[2][0])
    relinked = dict(rows[2][1])
    relinked["previous_hash"] = AuditModule._hash_entry(first)
    await redis.xadd("audit:messages", {key: value for key, value in relinked.items()})
    assert await audit.verify_integrity("message-2") is False
    await audit.close()


async def test_integrity_scan_budget_never_claims_partial_verification(
    redis: Redis, tmp_path: Path
) -> None:
    audit = _module(redis, tmp_path / "archive")
    for index in range(5):
        await _log(audit, index)
    with pytest.raises(AuditScanLimitError, match="integrity_scan_limit"):
        await audit.verify_integrity("message-0", max_entries=2)
    assert await audit.verify_integrity("message-0", max_entries=5)
    await audit.close()


async def test_security_payload_tampering_is_detected(redis: Redis) -> None:
    audit = AuditModule(redis, file_sink=None)
    await audit.log_security_event("denied", {"subject": "real"})
    row = _ROWS.validate_python(await redis.xrange("audit:security_events"))[0]
    await redis.xdel("audit:security_events", row[0])
    tampered = dict(row[1])
    tampered["details"] = '{"subject":"forged"}'
    await redis.xadd(
        "audit:security_events", {key: value for key, value in tampered.items()}
    )
    assert await audit.verify_security_integrity() is False
    await audit.close()


async def test_legacy_records_remain_readable_without_v2_verification(
    redis: Redis,
) -> None:
    audit = AuditModule(redis, file_sink=None)
    entry = AuditEntry(
        message_id="legacy",
        sender_id="sender",
        target_id="target",
        decision="ALLOWED",
        latency_ms=1,
        payload_hash="hash",
        hash_version=1,
    )
    fields = AuditModule._entry_to_stream_fields(entry)
    fields.pop("hash_version")
    await redis.xadd("audit:messages", {key: value for key, value in fields.items()})
    await redis.set("audit:last_hash", AuditModule._hash_entry(entry))
    assert (await audit.query_all())[0]["hash_version"] == 1
    assert await audit.verify_integrity("legacy") is False
    await audit.close()


async def test_invalid_index_type_cannot_partially_append(redis: Redis) -> None:
    audit = AuditModule(redis, file_sink=None)
    await redis.set("audit:by_target:target", "wrong type")
    with pytest.raises(ResponseError, match="invalid_audit_key_type"):
        await _log(audit, 0)
    assert await redis.exists("audit:messages") == 0
    assert await redis.exists("audit:last_hash") == 0
    assert await redis.exists("audit:by_sender:sender") == 0
    await audit.close()


async def test_operator_yaml_roundtrips_sentinel_durability_and_retention(
    tmp_path: Path,
) -> None:
    settings = GatewaySettings(
        redis=RedisSettings(
            sentinel=SentinelSettings(
                service_name="mas-primary", addresses=(("localhost", 26379),)
            ),
            durability=RedisDurabilitySettings(replica_count=1, wait_for_aof=True),
        ),
        audit=AuditSettings(
            retention=AuditRetentionSettings(
                max_messages=200_000, archive_directory=str(tmp_path / "archive")
            )
        ),
    )
    path = tmp_path / "operator.yaml"
    settings.to_yaml(str(path))
    assert "!!python" not in path.read_text()
    restored = GatewaySettings.from_yaml(str(path))
    assert restored.redis == settings.redis
    assert restored.audit == settings.audit


_CRASH_ARCHIVER = """
import asyncio
import sys
from mas_core.durability import RedisDurability, RedisDurabilitySettings
from mas_gateway.audit import AuditModule
from mas_gateway.audit_archive import AuditArchive, AuditRetentionSettings, AuditSegment
from redis.asyncio import Redis

class PausingArchive(AuditArchive):
    async def write(self, segment: AuditSegment) -> str:
        digest = await super().write(segment)
        print('archive_ready', flush=True)
        await asyncio.Event().wait()
        return digest

async def main() -> None:
    redis = Redis.from_url(sys.argv[1], decode_responses=True)
    audit = AuditModule(redis, file_sink=None,
        retention=AuditRetentionSettings(max_messages=2, batch_size=1,
            archive_directory=sys.argv[2]),
        durability=RedisDurability(RedisDurabilitySettings(wait_for_aof=True)))
    audit._archive = PausingArchive(sys.argv[2])
    await audit.log_message('message-2', 'sender', 'target', 'ALLOWED', 1.25, {})

asyncio.run(main())
"""


async def test_uint64_index_position_guard_runs_before_any_batch_write(
    tmp_path: Path,
) -> None:
    node = RedisNode(tmp_path / "redis")
    await node.start()
    redis = Redis.from_url(node.url, decode_responses=True)
    audit = AuditModule(redis, file_sink=None)
    try:
        entry = AuditEntry(
            message_id="existing",
            sender_id="sender",
            target_id="target",
            decision="ALLOWED",
            latency_ms=1,
            payload_hash="hash",
        )
        fields = AuditModule._entry_to_stream_fields(entry)
        head = AuditModule._hash_entry(entry)
        await redis.xadd(
            "audit:messages",
            {key: value for key, value in fields.items()},
            id=f"{2**64 - 2}-0",
        )
        await redis.xadd(
            "audit:by_target:target",
            {key: value for key, value in fields.items()},
            id=f"{2**64 - 1}-0",
        )
        await redis.set("audit:last_hash", head)
        with pytest.raises(ResponseError, match="invalid_audit_index_position"):
            await _log(audit, 0)
        assert await redis.xlen("audit:messages") == 1
        assert await redis.exists("audit:by_sender:sender") == 0
        assert await redis.get("audit:last_hash") == head
    finally:
        await audit.close()
        await redis.aclose()
        await node.stop()


async def test_sigkill_after_archive_fsync_keeps_prefix_until_atomic_checkpoint(
    tmp_path: Path,
) -> None:
    node = RedisNode(tmp_path / "redis")
    await node.start()
    redis = Redis.from_url(node.url, decode_responses=True)
    directory = tmp_path / "archive"
    audit = _module(redis, directory)
    process: asyncio.subprocess.Process | None = None
    try:
        await _log(audit, 0)
        await _log(audit, 1)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            _CRASH_ARCHIVER,
            node.url,
            str(directory),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None and process.stderr is not None
        marker = await asyncio.wait_for(process.stdout.readline(), 5)
        if marker != b"archive_ready\n":
            raise AssertionError((await process.stderr.read()).decode())
        process.kill()
        assert await process.wait() < 0
        assert len(list(directory.glob("*.json"))) == 1
        assert await redis.get("audit:messages:checkpoint") is None
        assert await redis.xlen("audit:messages") == 2
        await _log(audit, 2)
        assert await redis.xlen("audit:messages") == 2
        assert await audit.get_stats() == {"total_messages": 3, "security_events": 0}
        assert await audit.verify_integrity("message-0")
        assert len(await audit.query_all()) == 3
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        await audit.close()
        await redis.aclose()
        await node.stop()


async def test_peer_compaction_cannot_split_archive_checkpoint_and_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    node = RedisNode(tmp_path / "redis")
    await node.start()
    redis = Redis.from_url(node.url, decode_responses=True)
    peer_redis = Redis.from_url(node.url, decode_responses=True)
    directory = tmp_path / "archive"
    audit = _module(redis, directory)
    peer = _module(peer_redis, directory)
    raced = False
    original_get = redis.get
    original_pipeline = redis.pipeline

    async def peer_write_after_snapshot() -> None:
        nonlocal raced
        if not raced:
            raced = True
            await _log(peer, 2)

    async def get_then_peer_write(key: KeyT) -> str | None:
        result = _OPTIONAL_STRING.validate_python(await original_get(key))
        if key == "audit:messages:checkpoint":
            await peer_write_after_snapshot()
        return result

    def pipeline_then_peer_write(
        transaction: bool = True, shard_hint: str | None = None
    ) -> Pipeline:
        pipeline = original_pipeline(transaction=transaction, shard_hint=shard_hint)
        original_execute = pipeline.execute

        async def execute_then_peer_write(raise_on_error: bool = True) -> list[object]:
            result = _RESPONSES.validate_python(
                await original_execute(raise_on_error=raise_on_error)
            )
            if transaction:
                await peer_write_after_snapshot()
            return result

        monkeypatch.setattr(pipeline, "execute", execute_then_peer_write)
        return pipeline

    try:
        await _log(peer, 0)
        await _log(peer, 1)
        monkeypatch.setattr(redis, "get", get_then_peer_write)
        monkeypatch.setattr(redis, "pipeline", pipeline_then_peer_write)
        await _log(audit, 3)
        assert raced
        assert await redis.xlen("audit:messages") == 2
        assert await audit.get_stats() == {"total_messages": 4, "security_events": 0}
        assert [row["message_id"] for row in await audit.query_all()] == [
            "message-0",
            "message-1",
            "message-2",
            "message-3",
        ]
        assert await audit.verify_integrity("message-0")
        assert await audit.verify_integrity("message-3")
    finally:
        await asyncio.gather(audit.close(), peer.close())
        await redis.aclose()
        await peer_redis.aclose()
        await node.stop()

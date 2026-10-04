"""Real Redis regressions for cached audit heads and pipelined durability."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import ClassVar

import pytest
from mas_core.durability import (
    RedisDurability,
    RedisDurabilityError,
    RedisDurabilitySettings,
)
from mas_core.redis_commit import StreamAppend
from mas_gateway.audit import AuditModule
from mas_gateway.audit_archive import AuditArchiveError, AuditRetentionSettings
from pydantic import TypeAdapter
from redis.asyncio import Redis
from redis.exceptions import ResponseError
from redis.typing import EncodableT, KeyT

from integration_tests.production_support import RedisNode

pytestmark = pytest.mark.asyncio
_STRINGS = TypeAdapter(list[str])


class ObservedRedis(Redis):
    """Count client head reads while retaining the actual Redis implementation."""

    direct_reads: ClassVar[list[KeyT]] = []
    append_results: ClassVar[list[list[str]]] = []

    async def get(self, name: KeyT) -> bytes | str | None:
        self.direct_reads.append(name)
        return await super().get(name)

    async def eval(
        self, script: str, numkeys: int, *keys_and_args: KeyT | EncodableT
    ) -> list[str]:
        result = _STRINGS.validate_python(
            await super().eval(script, numkeys, *keys_and_args)
        )
        self.append_results.append(result)
        return result


class ObservedDurability(RedisDurability):
    """Count actual local AOF confirmations without changing their semantics."""

    def __init__(self) -> None:
        super().__init__(RedisDurabilitySettings(wait_for_aof=True))
        self.calls = 0

    async def confirm(self, connection: Redis) -> None:
        self.calls += 1
        await super().confirm(connection)


class DelayedDurability(ObservedDurability):
    """Keep confirmed receipts pending to exercise overlapping confirmation work."""

    def __init__(self) -> None:
        super().__init__()
        self.active = 0
        self.parallel = asyncio.Event()
        self.release = asyncio.Event()

    async def confirm(self, connection: Redis) -> None:
        await super().confirm(connection)
        self.active += 1
        if self.active == 2:
            self.parallel.set()
        try:
            await self.release.wait()
        finally:
            self.active -= 1


@pytest.fixture
async def private_redis(tmp_path: Path) -> AsyncIterator[ObservedRedis]:
    node = RedisNode(tmp_path / "redis")
    await node.start()
    ObservedRedis.direct_reads.clear()
    ObservedRedis.append_results.clear()
    try:
        async with ObservedRedis(
            host="127.0.0.1", port=node.port, decode_responses=True
        ) as client:
            yield client
    finally:
        await node.stop()


async def _message(
    audit: AuditModule, index: int, *, stream_append: StreamAppend | None = None
) -> str:
    return await audit.log_message(
        f"message-{index}",
        "sender",
        "target",
        "ALLOWED",
        1.0,
        {"index": index},
        stream_append=stream_append,
    )


async def test_head_cache_recovers_external_writes_without_client_gets(
    private_redis: ObservedRedis,
) -> None:
    barriers = [ObservedDurability(), ObservedDurability()]
    audits = [
        AuditModule(
            private_redis,
            file_sink=None,
            retention=AuditRetentionSettings(batch_size=4),
            durability=barrier,
        )
        for barrier in barriers
    ]
    try:
        for index in range(12):
            assert await _message(audits[index % 2], index)
        assert private_redis.direct_reads == []
        assert sum(barrier.calls for barrier in barriers) == 12
        assert (
            sum(result[0] == "retry" for result in private_redis.append_results) == 11
        )
        assert await audits[0].verify_integrity("message-0")
        assert await audits[1].verify_integrity("message-11")
        assert await private_redis.xlen("audit:messages") == 12
    finally:
        await asyncio.gather(*(audit.close() for audit in audits))


async def test_local_append_lane_advances_head_while_receipts_are_pending(
    private_redis: ObservedRedis,
) -> None:
    barrier = DelayedDurability()
    audit = AuditModule(
        private_redis,
        file_sink=None,
        durability=barrier,
        retention=AuditRetentionSettings(batch_size=1),
    )
    messages = [
        asyncio.create_task(
            _message(
                audit,
                index,
                stream_append=StreamAppend("queue", (("message", str(index)),)),
            )
        )
        for index in range(2)
    ]
    try:
        async with asyncio.timeout(2):
            await barrier.parallel.wait()
        assert barrier.calls == 2
        assert [result[0] for result in private_redis.append_results] == ["ok", "ok"]
        assert not any(message.done() for message in messages)
        assert await private_redis.xlen("audit:messages") == 2
        assert await private_redis.xlen("queue") == 2
        barrier.release.set()
        async with asyncio.timeout(2):
            receipts = await asyncio.gather(*messages)
            await audit.close()
        assert len(set(receipts)) == 2
        assert await audit.verify_integrity("message-1")
    finally:
        barrier.release.set()
        await asyncio.gather(*messages, return_exceptions=True)
        await audit.close()


async def test_cached_head_does_not_allow_corrupt_external_head(
    private_redis: ObservedRedis,
) -> None:
    audit = AuditModule(private_redis, file_sink=None)
    try:
        await _message(audit, 0)
        await private_redis.set("audit:last_hash", "invalid")
        with pytest.raises(AuditArchiveError, match="invalid_audit_chain_head"):
            await _message(audit, 1)
        assert await private_redis.xlen("audit:messages") == 1
        assert await private_redis.xlen("audit:by_sender:sender") == 1
        assert private_redis.direct_reads == []
    finally:
        await audit.close()


async def test_unconfirmed_batch_has_no_receipt_and_next_writer_recovers_head(
    private_redis: ObservedRedis,
) -> None:
    uncertain = AuditModule(
        private_redis,
        file_sink=None,
        durability=RedisDurability(
            RedisDurabilitySettings(replica_count=1, wait_for_aof=True, timeout_ms=20)
        ),
    )
    confirmed = AuditModule(
        private_redis,
        file_sink=None,
        durability=RedisDurability(RedisDurabilitySettings(wait_for_aof=True)),
    )
    try:
        with pytest.raises(RedisDurabilityError, match="durability_unconfirmed"):
            await _message(
                uncertain, 0, stream_append=StreamAppend("queue", (("message", "0"),))
            )
        assert await private_redis.xlen("audit:messages") == 1
        assert await private_redis.xlen("queue") == 1
        assert await _message(
            confirmed, 1, stream_append=StreamAppend("queue", (("message", "1"),))
        )
        assert await confirmed.verify_integrity("message-0")
        assert await confirmed.verify_integrity("message-1")
    finally:
        await asyncio.gather(uncertain.close(), confirmed.close())


async def test_concurrent_aof_batches_survive_sigkill(tmp_path: Path) -> None:
    node = RedisNode(tmp_path / "redis")
    await node.start()
    try:
        async with Redis.from_url(node.url, decode_responses=True) as client:
            audits = [
                AuditModule(
                    client,
                    file_sink=None,
                    retention=AuditRetentionSettings(batch_size=8),
                    durability=RedisDurability(
                        RedisDurabilitySettings(wait_for_aof=True)
                    ),
                )
                for _ in range(2)
            ]
            try:
                receipts = await asyncio.gather(
                    *(
                        _message(
                            audits[index % 2],
                            index,
                            stream_append=StreamAppend(
                                "queue", (("message", str(index)),)
                            ),
                        )
                        for index in range(64)
                    )
                )
                assert len(set(receipts)) == 64
                assert await audits[0].verify_integrity("message-63")
            finally:
                await asyncio.gather(*(audit.close() for audit in audits))
        await node.stop(crash=True)
        await node.start()
        async with Redis.from_url(node.url, decode_responses=True) as client:
            recovered = AuditModule(client, file_sink=None)
            try:
                assert await client.xlen("audit:messages") == 64
                assert await client.xlen("queue") == 64
                assert await recovered.verify_integrity("message-0")
                assert await recovered.verify_integrity("message-63")
                assert await _message(recovered, 64)
                assert await recovered.verify_integrity("message-64")
            finally:
                await recovered.close()
    finally:
        await node.stop()


async def test_invalid_effect_type_rejects_entire_audit_batch(
    private_redis: ObservedRedis,
) -> None:
    audit = AuditModule(
        private_redis, file_sink=None, retention=AuditRetentionSettings(batch_size=4)
    )
    await private_redis.set("invalid-queue", "wrong-type")
    try:
        outcomes = await asyncio.gather(
            _message(
                audit, 0, stream_append=StreamAppend("queue", (("message", "0"),))
            ),
            _message(
                audit,
                1,
                stream_append=StreamAppend("invalid-queue", (("message", "1"),)),
            ),
            return_exceptions=True,
        )
        assert all(isinstance(outcome, ResponseError) for outcome in outcomes)
        assert await private_redis.xlen("audit:messages") == 0
        assert await private_redis.xlen("audit:by_sender:sender") == 0
        assert await private_redis.xlen("queue") == 0
        assert await private_redis.get("audit:last_hash") is None
    finally:
        await audit.close()


async def test_effect_stream_id_capacity_preflight_prevents_partial_batch(
    private_redis: ObservedRedis,
) -> None:
    audit = AuditModule(
        private_redis, file_sink=None, retention=AuditRetentionSettings(batch_size=4)
    )
    original_id = "18446744073709551615-18446744073709551614"
    await private_redis.xadd("queue", {"message": "existing"}, id=original_id)
    try:
        outcomes = await asyncio.gather(
            *(
                _message(
                    audit,
                    index,
                    stream_append=StreamAppend("queue", (("message", str(index)),)),
                )
                for index in range(2)
            ),
            return_exceptions=True,
        )
        assert all(
            isinstance(outcome, ResponseError)
            and "audit_stream_id_exhausted" in str(outcome)
            for outcome in outcomes
        )
        assert await private_redis.xlen("audit:messages") == 0
        assert await private_redis.xlen("queue") == 1
        assert await private_redis.get("audit:last_hash") is None
    finally:
        await audit.close()


@pytest.mark.parametrize("decision", ["ALLOWED", "ALERT", "DLP_REDACTED"])
async def test_successful_policy_decisions_commit_audit_and_effect(
    private_redis: ObservedRedis, decision: str
) -> None:
    audit = AuditModule(private_redis, file_sink=None)
    try:
        await audit.log_message(
            "message",
            "sender",
            "target",
            decision,
            1.0,
            {},
            stream_append=StreamAppend("queue", (("message", "payload"),)),
        )
        assert await private_redis.xlen("audit:messages") == 1
        assert await private_redis.xlen("queue") == 1
        assert await audit.verify_integrity("message")
    finally:
        await audit.close()


@pytest.mark.parametrize(
    "decision", ["AUTHZ_DENIED", "RATE_LIMITED", "CIRCUIT_OPEN", "DLP_BLOCKED"]
)
async def test_denied_policy_decisions_cannot_commit_effect(
    private_redis: ObservedRedis, decision: str
) -> None:
    audit = AuditModule(private_redis, file_sink=None)
    try:
        with pytest.raises(ValueError, match="routable decision"):
            await audit.log_message(
                "message",
                "sender",
                "target",
                decision,
                1.0,
                {},
                stream_append=StreamAppend("queue", (("message", "payload"),)),
            )
        assert await private_redis.xlen("audit:messages") == 0
        assert await private_redis.xlen("queue") == 0
    finally:
        await audit.close()


async def test_commit_effect_cannot_corrupt_other_audit_chain(
    private_redis: ObservedRedis,
) -> None:
    audit = AuditModule(private_redis, file_sink=None)
    try:
        with pytest.raises(ValueError, match="non-audit stream"):
            await _message(
                audit,
                0,
                stream_append=StreamAppend(
                    "audit:security_events", (("message", "0"),)
                ),
            )
        assert await private_redis.xlen("audit:messages") == 0
        assert await private_redis.xlen("audit:security_events") == 0
    finally:
        await audit.close()

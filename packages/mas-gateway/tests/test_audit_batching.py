"""Concurrent audit integrity, durability receipts and drain guarantees."""

from __future__ import annotations

import asyncio

import pytest
from mas_core.durability import RedisDurability
from mas_gateway.audit import AuditModule
from mas_gateway.audit_archive import AuditRetentionSettings
from redis.asyncio import Redis


class BlockingDurability(RedisDurability):
    """Hold real append receipts while observing independent confirmation groups."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.parallel = asyncio.Event()
        self.release = asyncio.Event()
        self.active = 0
        self.peak = 0

    async def confirm(self, connection: Redis) -> None:
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.entered.set()
        if self.active >= 2:
            self.parallel.set()
        try:
            await self.release.wait()
        finally:
            self.active -= 1


async def message(audit: AuditModule, index: int) -> str:
    return await audit.log_message(
        f"message-{index}", "sender", "target", "ALLOWED", 1.0, {"index": index}
    )


async def test_concurrent_message_and_security_batches_preserve_both_chains(
    redis: Redis,
) -> None:
    audit = AuditModule(
        redis, file_sink=None, retention=AuditRetentionSettings(batch_size=4)
    )
    try:
        message_ids, event_ids = await asyncio.gather(
            asyncio.gather(*(message(audit, index) for index in range(25))),
            asyncio.gather(
                *(
                    audit.log_security_event(
                        "ACCESS_ALLOWED", {"operator_id": f"operator-{index}"}
                    )
                    for index in range(25)
                )
            ),
        )
        assert len(set(message_ids)) == len(set(event_ids)) == 25
        assert await redis.xlen("audit:messages") == 25
        assert await redis.xlen("audit:security_events") == 25
        assert await audit.verify_integrity("message-0")
        assert await audit.verify_integrity("message-24")
        assert await audit.verify_security_integrity()
        assert len(await audit.query_by_sender("sender")) == 25
        assert len(await audit.query_by_target("target")) == 25
    finally:
        await audit.close()


async def test_confirmation_groups_overlap_and_close_waits_for_receipts(
    redis: Redis,
) -> None:
    durability = BlockingDurability()
    audit = AuditModule(
        redis,
        file_sink=None,
        durability=durability,
        retention=AuditRetentionSettings(batch_size=3),
    )
    tasks = [asyncio.create_task(message(audit, index)) for index in range(12)]
    closing: asyncio.Task[None] | None = None
    try:
        async with asyncio.timeout(2):
            await durability.parallel.wait()
        assert durability.peak >= 2
        assert not any(task.done() for task in tasks)
        closing = asyncio.create_task(audit.close())
        await asyncio.sleep(0)
        assert not closing.done()
        durability.release.set()
        async with asyncio.timeout(2):
            receipts = await asyncio.gather(*tasks)
            await closing
        assert len(set(receipts)) == 12
        assert await redis.xlen("audit:messages") == 12
        assert await audit.verify_integrity("message-11")
    finally:
        durability.release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        if closing is not None:
            await closing
        await audit.close()


async def test_cancellation_after_append_does_not_orphan_the_audit_worker(
    redis: Redis,
) -> None:
    durability = BlockingDurability()
    audit = AuditModule(
        redis,
        file_sink=None,
        durability=durability,
        retention=AuditRetentionSettings(batch_size=2),
    )
    canceled = asyncio.create_task(message(audit, 0))
    survivor: asyncio.Task[str] | None = None
    try:
        async with asyncio.timeout(2):
            await durability.entered.wait()
        canceled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await canceled
        survivor = asyncio.create_task(message(audit, 1))
        durability.release.set()
        async with asyncio.timeout(2):
            await survivor
            await audit.close()
        assert await redis.xlen("audit:messages") == 2
        assert await audit.verify_integrity("message-0")
        assert await audit.verify_integrity("message-1")
    finally:
        durability.release.set()
        await asyncio.gather(
            canceled, *([survivor] if survivor else []), return_exceptions=True
        )
        await audit.close()


async def test_blocked_barriers_bound_unconfirmed_work_and_resume_waiters(
    redis: Redis,
) -> None:
    durability = BlockingDurability()
    audit = AuditModule(
        redis,
        file_sink=None,
        durability=durability,
        retention=AuditRetentionSettings(batch_size=3),
    )
    tasks = [asyncio.create_task(message(audit, index)) for index in range(40)]
    try:
        async with asyncio.timeout(2):
            await durability.parallel.wait()
        await asyncio.sleep(0)
        assert durability.peak == 2
        assert await redis.xlen("audit:messages") <= 6
        assert not any(task.done() for task in tasks)
        durability.release.set()
        async with asyncio.timeout(2):
            receipts = await asyncio.gather(*tasks)
        assert len(set(receipts)) == 40
        assert await audit.verify_integrity("message-39")
    finally:
        durability.release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await audit.close()

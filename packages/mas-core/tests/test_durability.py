"""Real Redis durability barriers, including uncertain committed writes."""

from pathlib import Path

import pytest
from mas_core.durability import (
    RedisDurability,
    RedisDurabilityError,
    RedisDurabilitySettings,
)
from redis.asyncio import Redis

from integration_tests.production_support import RedisNode


@pytest.mark.asyncio
async def test_aof_confirmed_write_survives_sigkill(tmp_path: Path) -> None:
    node = RedisNode(tmp_path)
    await node.start()
    try:
        async with (
            Redis.from_url(node.url, decode_responses=True) as client,
            client.client() as connection,
        ):
            await connection.set("confirmed", "payload")
            await RedisDurability(RedisDurabilitySettings(wait_for_aof=True)).confirm(
                connection
            )
        await node.stop(crash=True)
        await node.start()
        async with Redis.from_url(node.url, decode_responses=True) as client:
            assert await client.get("confirmed") == "payload"
    finally:
        await node.stop()


@pytest.mark.asyncio
async def test_unavailable_replica_is_uncertain_commit(tmp_path: Path) -> None:
    node = RedisNode(tmp_path)
    await node.start()
    try:
        async with (
            Redis.from_url(node.url, decode_responses=True) as client,
            client.client() as connection,
        ):
            await connection.set("uncertain", "payload")
            with pytest.raises(RedisDurabilityError, match="durability_unconfirmed"):
                await RedisDurability(
                    RedisDurabilitySettings(replica_count=1, timeout_ms=20)
                ).confirm(connection)
            assert await connection.get("uncertain") == "payload"
    finally:
        await node.stop()


@pytest.mark.asyncio
async def test_connection_failure_at_barrier_is_uncertain_commit(
    tmp_path: Path,
) -> None:
    node = RedisNode(tmp_path)
    await node.start()
    try:
        async with (
            Redis.from_url(
                node.url, decode_responses=True, socket_connect_timeout=0.1
            ) as client,
            client.client() as connection,
        ):
            await connection.set("uncertain", "committed")
            await node.stop(crash=True)
            with pytest.raises(RedisDurabilityError, match="durability_unconfirmed"):
                await RedisDurability(
                    RedisDurabilitySettings(wait_for_aof=True)
                ).confirm(connection)
        await node.start()
        async with Redis.from_url(node.url, decode_responses=True) as client:
            assert await client.get("uncertain") == "committed"
    finally:
        await node.stop()


@pytest.mark.asyncio
async def test_pipelined_aof_writes_survive_sigkill(tmp_path: Path) -> None:
    node = RedisNode(tmp_path)
    await node.start()
    try:
        async with (
            Redis.from_url(node.url, decode_responses=True) as client,
            client.pipeline(transaction=False) as pipeline,
        ):
            pipeline.set("confirmed:first", "one")
            pipeline.set("confirmed:second", "two")
            results = await RedisDurability(
                RedisDurabilitySettings(wait_for_aof=True)
            ).execute(pipeline)
            assert results == [True, True]
        await node.stop(crash=True)
        await node.start()
        async with Redis.from_url(node.url, decode_responses=True) as client:
            assert await client.mget("confirmed:first", "confirmed:second") == [
                "one",
                "two",
            ]
    finally:
        await node.stop()


@pytest.mark.asyncio
async def test_pipeline_missing_replica_does_not_report_acceptance(
    tmp_path: Path,
) -> None:
    node = RedisNode(tmp_path)
    await node.start()
    try:
        async with Redis.from_url(node.url, decode_responses=True) as client:
            async with client.pipeline(transaction=False) as pipeline:
                pipeline.set("uncertain", "payload")
                with pytest.raises(
                    RedisDurabilityError, match="durability_unconfirmed"
                ):
                    await RedisDurability(
                        RedisDurabilitySettings(
                            wait_for_aof=True, replica_count=1, timeout_ms=20
                        )
                    ).execute(pipeline)
            assert await client.get("uncertain") == "payload"
    finally:
        await node.stop()


@pytest.mark.asyncio
async def test_transactional_barrier_is_rejected_before_writes(redis: Redis) -> None:
    async with redis.pipeline() as pipeline:
        pipeline.set("not-confirmed", "payload")
        with pytest.raises(ValueError, match="nontransactional"):
            await RedisDurability(RedisDurabilitySettings(wait_for_aof=True)).execute(
                pipeline
            )
    assert await redis.get("not-confirmed") is None

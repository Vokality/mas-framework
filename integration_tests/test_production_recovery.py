"""Actual Sentinel election and snapshot restoration of MAS data contracts."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import sys
import time
from pathlib import Path

import pytest
from mas_core.durability import RedisDurability, RedisDurabilitySettings
from mas_core.protocol import EnvelopeMessage
from mas_core.redis_client import SentinelSettings, create_redis_client
from mas_gateway.config import GatewaySettings, RedisSettings
from mas_server.cli import BrokerReady
from mas_server.dev import dev_server_settings, generate_dev_tls
from mas_server.routing import MessageRouter
from mas_server.state import StateStore
from pydantic import TypeAdapter
from redis.asyncio import Redis
from redis.exceptions import RedisError

from integration_tests.production_support import RedisNode, RedisTopology

_STREAM_ENTRIES = TypeAdapter(list[tuple[str, dict[str, str]]])


@pytest.mark.asyncio
async def test_primary_crash_preserves_confirmed_queue_and_state(
    tmp_path: Path,
) -> None:
    topology = RedisTopology(tmp_path)
    await topology.start()
    try:
        sentinel = SentinelSettings(
            service_name="mas-primary",
            addresses=tuple(("127.0.0.1", node.port) for node in topology.sentinels),
        )
        # Finite batches may receive replica fsync ACKs on Redis's one-second cron.
        # Cover that cadence and scheduling jitter without changing fsync requirements.
        policy = RedisDurabilitySettings(
            replica_count=1, timeout_ms=2000, wait_for_aof=True
        )
        async with create_redis_client(url=None, sentinel=sentinel) as redis:
            router = MessageRouter(
                redis=redis, dlq_enabled=True, durability=RedisDurability(policy)
            )
            state = StateStore(redis, durability=policy)
            messages = [
                EnvelopeMessage(
                    sender_id="producer",
                    target_id="worker",
                    message_type="work",
                    data={"sequence": sequence},
                    message_id=f"accepted-{sequence}",
                )
                for sequence in range(128)
            ]
            for offset in range(0, len(messages), 16):
                await asyncio.gather(
                    *(
                        router.route_message(message)
                        for message in messages[offset : offset + 16]
                    )
                )
            assert (
                await state.update_state(
                    agent_id="worker",
                    updates={"checkpoint": "128"},
                    expected_revision=0,
                )
                == 1
            )
            started = time.monotonic()
            election_seconds = await topology.crash_primary()
            assert election_seconds <= 10
            async with asyncio.timeout(max(0.001, 10 - (time.monotonic() - started))):
                while True:
                    try:
                        entries = _STREAM_ENTRIES.validate_python(
                            await redis.xrange("agent.stream:worker")
                        )
                        snapshot = await state.snapshot(agent_id="worker")
                        break
                    except RedisError:
                        await asyncio.sleep(0.02)
            assert time.monotonic() - started <= 10
            recovered = {
                EnvelopeMessage.model_validate_json(fields["envelope"]).message_id
                for _, fields in entries
            }
            assert recovered == {message.message_id for message in messages}
            assert snapshot.fields == {"checkpoint": "128"}
            assert snapshot.revision == 1
            # The new primary must also confirm a fresh write to its remaining replica.
            async with asyncio.timeout(max(0.001, 10 - (time.monotonic() - started))):
                while True:
                    try:
                        await router.route_message(
                            EnvelopeMessage(
                                sender_id="producer",
                                target_id="worker",
                                message_type="work",
                                data={},
                                message_id="post-failover",
                            )
                        )
                        break
                    except RedisError:
                        await asyncio.sleep(0.02)
            assert time.monotonic() - started <= 10
    finally:
        await topology.stop()


@pytest.mark.asyncio
async def test_completed_snapshot_restores_queued_work_and_state(
    tmp_path: Path,
) -> None:
    source = RedisNode(tmp_path / "source")
    restored = RedisNode(tmp_path / "restored", appendonly=False)
    await source.start()
    try:
        async with Redis.from_url(source.url, decode_responses=True) as redis:
            router = MessageRouter(redis=redis, dlq_enabled=True)
            state = StateStore(redis)
            for sequence in range(12):
                await router.route_message(
                    EnvelopeMessage(
                        sender_id="producer",
                        target_id="worker",
                        message_type="work",
                        data={"sequence": sequence},
                        message_id=f"backup-{sequence}",
                    )
                )
            await state.update_state(
                agent_id="worker", updates={"checkpoint": "12"}, expected_revision=0
            )
            before = _STREAM_ENTRIES.validate_python(
                await redis.xrange("agent.stream:worker")
            )
        snapshot = await source.snapshot(tmp_path / "backup")
        checksum = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        restored.directory.mkdir(parents=True)
        target = restored.directory / "dump.rdb"
        shutil.copy2(snapshot, target)
        assert hashlib.sha256(target.read_bytes()).hexdigest() == checksum
        await restored.start()
        async with Redis.from_url(restored.url, decode_responses=True) as redis:
            assert (
                _STREAM_ENTRIES.validate_python(
                    await redis.xrange("agent.stream:worker")
                )
                == before
            )
            state = await StateStore(redis).snapshot(agent_id="worker")
            assert state.fields == {"checkpoint": "12"}
            assert state.revision == 1
    finally:
        await restored.stop()
        await source.stop()


@pytest.mark.asyncio
async def test_broker_process_entrypoint_handles_supervisor_shutdown(
    tmp_path: Path,
) -> None:
    node = RedisNode(tmp_path / "redis")
    await node.start()
    process: asyncio.subprocess.Process | None = None
    try:
        tls = await asyncio.to_thread(generate_dev_tls, tmp_path / "tls")
        settings = dev_server_settings(agents={}, tls=tls, listen_addr="127.0.0.1:0")
        broker_path = tmp_path / "broker.json"
        broker_path.write_bytes(TypeAdapter(type(settings)).dump_json(settings))
        gateway_path = tmp_path / "gateway.yaml"
        GatewaySettings(redis=RedisSettings(url=node.url)).to_yaml(str(gateway_path))
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "mas_server",
            "--settings",
            str(broker_path),
            "--gateway",
            str(gateway_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stderr is not None
        async with asyncio.timeout(5):
            while b"Broker ready" not in await process.stderr.readline():
                if process.returncode is not None:
                    pytest.fail("Broker exited before readiness")
        assert process.stdout is not None
        ready = BrokerReady.model_validate_json(
            await asyncio.wait_for(process.stdout.readline(), timeout=2)
        )
        assert ready.pid == process.pid
        assert ready.listen_addr.startswith("127.0.0.1:")
        assert not ready.listen_addr.endswith(":0")
        assert ready.management_url is None
        process.terminate()
        assert await asyncio.wait_for(process.wait(), timeout=5) == 0
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        await node.stop()

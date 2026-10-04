"""Disposable real infrastructure for production acceptance checks."""

from __future__ import annotations

import asyncio
import json
import shutil
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

from mas_core.durability import RedisDurability, RedisDurabilitySettings
from pydantic import BaseModel, TypeAdapter
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError


class ReplicationState(BaseModel):
    """Validated fields used to wait for an actual replication topology."""

    role: str
    master_link_status: str | None = None
    connected_slaves: int = 0


class PersistenceState(BaseModel):
    """Validated completion state of a Redis snapshot."""

    rdb_bgsave_in_progress: int
    rdb_last_bgsave_status: str


@dataclass(slots=True)
class RedisNode:
    """Own a Redis process with private files and an ephemeral TCP port."""

    directory: Path
    appendonly: bool = True
    port: int = 0
    process: asyncio.subprocess.Process | None = field(default=None, repr=False)

    @property
    def url(self) -> str:
        """Return this private node's endpoint."""
        if not self.port:
            raise RuntimeError("Redis node has not started")
        return f"redis://127.0.0.1:{self.port}/0"

    async def start(self, *, primary: RedisNode | None = None) -> None:
        """Start a real process, optionally as a replicating secondary."""
        executable = shutil.which("redis-server")
        if executable is None:
            raise RuntimeError("Production validation requires redis-server >=7.2")
        self.directory.mkdir(parents=True, exist_ok=True)
        if self.port == 0:
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                self.port = reservation.getsockname()[1]
        configuration = [
            "bind 127.0.0.1",
            f"port {self.port}",
            f"dir {json.dumps(str(self.directory))}",
            'save ""',
            f"appendonly {'yes' if self.appendonly else 'no'}",
            "appendfsync always",
            "maxmemory-policy noeviction",
            "repl-diskless-sync-delay 0",
            f"logfile {json.dumps(str(self.directory / 'redis.log'))}",
        ]
        if primary is not None:
            configuration.append(f"replicaof 127.0.0.1 {primary.port}")
        path = self.directory / "redis.conf"
        path.write_text("\n".join(configuration) + "\n")
        self.process = await asyncio.create_subprocess_exec(
            executable,
            str(path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            async with (
                Redis.from_url(self.url, socket_timeout=1) as client,
                asyncio.timeout(10),
            ):
                while True:
                    if self.process.returncode is not None:
                        raise RuntimeError((self.directory / "redis.log").read_text())
                    try:
                        await client.ping()
                        return
                    except (RedisConnectionError, RedisTimeoutError):
                        await asyncio.sleep(0.02)
        except BaseException:
            await self.stop()
            raise

    async def stop(self, *, crash: bool = False) -> None:
        """Stop only the owned process; crash mode deliberately skips shutdown."""
        process = self.process
        if process is not None:
            if process.returncode is None:
                if crash:
                    process.kill()
                else:
                    process.terminate()
            await process.wait()
            self.process = None

    async def snapshot(self, destination: Path) -> Path:
        """Save and copy a completed point-in-time snapshot from this node."""
        async with Redis.from_url(self.url, decode_responses=True) as client:
            await client.bgsave()
            async with asyncio.timeout(10):
                while True:
                    state = PersistenceState.model_validate(
                        await client.info("persistence")
                    )
                    if state.rdb_bgsave_in_progress == 0:
                        if state.rdb_last_bgsave_status != "ok":
                            raise RuntimeError("Redis snapshot failed")
                        break
                    await asyncio.sleep(0.02)
        destination.mkdir(parents=True, exist_ok=True)
        snapshot = destination / "dump.rdb"
        shutil.copy2(self.directory / "dump.rdb", snapshot)
        return snapshot


@dataclass(slots=True)
class SentinelNode:
    """Own one Sentinel participating in a real automatic failover."""

    directory: Path
    primary: RedisNode
    port: int = 0
    process: asyncio.subprocess.Process | None = field(default=None, repr=False)

    async def start(self) -> None:
        """Start a Sentinel with a two-vote quorum."""
        executable = shutil.which("redis-server")
        if executable is None:
            raise RuntimeError("Production validation requires redis-server")
        self.directory.mkdir(parents=True, exist_ok=True)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            self.port = reservation.getsockname()[1]
        path = self.directory / "sentinel.conf"
        path.write_text(
            "\n".join(
                [
                    "bind 127.0.0.1",
                    f"port {self.port}",
                    f"dir {json.dumps(str(self.directory))}",
                    f"logfile {json.dumps(str(self.directory / 'sentinel.log'))}",
                    f"sentinel monitor mas-primary 127.0.0.1 {self.primary.port} 2",
                    "sentinel down-after-milliseconds mas-primary 2000",
                    "sentinel failover-timeout mas-primary 5000",
                    "sentinel parallel-syncs mas-primary 1",
                ]
            )
            + "\n"
        )
        self.process = await asyncio.create_subprocess_exec(
            executable,
            str(path),
            "--sentinel",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            async with (
                Redis(host="127.0.0.1", port=self.port, socket_timeout=1) as client,
                asyncio.timeout(5),
            ):
                while True:
                    if self.process.returncode is not None:
                        raise RuntimeError(
                            (self.directory / "sentinel.log").read_text()
                        )
                    try:
                        await client.ping()
                        return
                    except (RedisConnectionError, RedisTimeoutError):
                        await asyncio.sleep(0.02)
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        """Terminate only this owned Sentinel process."""
        process = self.process
        if process is not None:
            if process.returncode is None:
                process.terminate()
            await process.wait()
            self.process = None


@dataclass(slots=True)
class RedisTopology:
    """One AOF primary, two AOF replicas and three failover voters."""

    directory: Path
    nodes: list[RedisNode] = field(default_factory=list)
    sentinels: list[SentinelNode] = field(default_factory=list)

    async def start(self) -> None:
        """Wait for the entire replication and discovery topology."""
        primary = RedisNode(self.directory / "primary")
        self.nodes.append(primary)
        try:
            await primary.start()
            for name in ["replica-one", "replica-two"]:
                node = RedisNode(self.directory / name)
                self.nodes.append(node)
                await node.start(primary=primary)
            async with asyncio.timeout(10):
                for replica in self.nodes[1:]:
                    async with Redis.from_url(replica.url) as client:
                        while True:
                            state = ReplicationState.model_validate(
                                await client.info("replication")
                            )
                            if state.master_link_status == "up":
                                break
                            await asyncio.sleep(0.02)
            for index in range(3):
                sentinel = SentinelNode(
                    self.directory / f"sentinel-{index}", primary=primary
                )
                self.sentinels.append(sentinel)
                await sentinel.start()
            async with (
                Redis.from_url(primary.url) as client,
                client.client() as connection,
            ):
                await connection.set("acceptance:topology_ready", "1")
                await RedisDurability(
                    RedisDurabilitySettings(
                        replica_count=2, timeout_ms=5000, wait_for_aof=True
                    )
                ).confirm(connection)
            # Sentinel discovers replicas on its periodic INFO polling cycle.
            async with asyncio.timeout(10):
                for sentinel in self.sentinels:
                    async with Redis(host="127.0.0.1", port=sentinel.port) as client:
                        while len(await client.sentinel_slaves("mas-primary")) < 2:
                            await asyncio.sleep(0.05)
        except BaseException:
            await self.stop()
            raise

    async def crash_primary(self) -> float:
        """Kill the primary and measure actual election completion."""
        primary = self.nodes[0]
        start = time.monotonic()
        await primary.stop(crash=True)
        sentinel = self.sentinels[0]
        async with (
            Redis(host="127.0.0.1", port=sentinel.port) as client,
            asyncio.timeout(10),
        ):
            while True:
                address = TypeAdapter(tuple[str, int] | None).validate_python(
                    await client.sentinel_get_master_addr_by_name("mas-primary")
                )
                if address is not None and address[1] != primary.port:
                    break
                await asyncio.sleep(0.02)
        return time.monotonic() - start

    async def stop(self) -> None:
        """Release all processes, including after partial startup."""
        await asyncio.gather(*(sentinel.stop() for sentinel in self.sentinels))
        await asyncio.gather(*(node.stop() for node in self.nodes))

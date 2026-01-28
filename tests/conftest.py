"""Shared pytest fixtures and configuration for all tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import textwrap
from typing import AsyncGenerator, Awaitable, Callable

import pytest
from redis.asyncio import Redis

from mas.agent import TlsClientConfig
from mas.gateway.config import GatewaySettings, RedisSettings
from mas.server import AgentDefinition, MASServer, MASServerSettings, TlsConfig

# Use anyio for async test support
pytestmark = pytest.mark.asyncio


def _run_openssl(args: list[str], *, cwd: Path) -> None:
    proc = subprocess.run(
        ["openssl", *args],
        cwd=str(cwd),
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "openssl failed: "
            + " ".join(args)
            + "\nstdout:\n"
            + proc.stdout
            + "\nstderr:\n"
            + proc.stderr
        )


def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


@dataclass(slots=True)
class TestTlsPaths:
    base_dir: Path
    ca_pem: str
    ca_key: str
    server_cert: str
    server_key: str

    def client(self, agent_id: str) -> TlsClientConfig:
        self._ensure_agent_cert(agent_id)
        return TlsClientConfig(
            root_ca_path=self.ca_pem,
            client_cert_path=str(self.base_dir / f"{agent_id}.pem"),
            client_key_path=str(self.base_dir / f"{agent_id}.key"),
        )

    def _ensure_agent_cert(self, agent_id: str) -> None:
        cert_path = self.base_dir / f"{agent_id}.pem"
        key_path = self.base_dir / f"{agent_id}.key"
        if cert_path.exists() and key_path.exists():
            return

        csr_path = self.base_dir / f"{agent_id}.csr"
        conf_path = self.base_dir / f"{agent_id}.cnf"

        _write_text(
            conf_path,
            textwrap.dedent(
                f"""
                [req]
                distinguished_name = dn
                req_extensions = req_ext
                prompt = no

                [dn]
                CN = {agent_id}

                [req_ext]
                keyUsage = critical, digitalSignature, keyEncipherment
                extendedKeyUsage = clientAuth
                subjectAltName = @alt_names

                [alt_names]
                URI.1 = spiffe://mas/agent/{agent_id}
                """
            ).lstrip(),
        )

        _run_openssl(["genrsa", "-out", str(key_path), "2048"], cwd=self.base_dir)
        _run_openssl(
            [
                "req",
                "-new",
                "-key",
                str(key_path),
                "-out",
                str(csr_path),
                "-config",
                str(conf_path),
            ],
            cwd=self.base_dir,
        )
        _run_openssl(
            [
                "x509",
                "-req",
                "-in",
                str(csr_path),
                "-CA",
                self.ca_pem,
                "-CAkey",
                self.ca_key,
                "-CAcreateserial",
                "-out",
                str(cert_path),
                "-days",
                "3650",
                "-sha256",
                "-extensions",
                "req_ext",
                "-extfile",
                str(conf_path),
            ],
            cwd=self.base_dir,
        )


@pytest.fixture
async def redis():
    """
    Redis connection fixture.

    Provides a Redis connection that is cleaned up after each test.
    Flushes the database before and after each test to ensure isolation.
    """
    r = Redis.from_url("redis://localhost:6379", decode_responses=True)
    # Ensure a clean DB at the start of each test.
    await r.flushdb()
    yield r
    # Cleanup
    await r.flushdb()
    await r.aclose()  # type: ignore[unresolved-attribute]


@pytest.fixture(autouse=True)
async def cleanup_agent_keys():
    """
    Auto-use fixture to clean up Redis agent keys and streams before each test.

    This ensures tests don't interfere with each other by cleaning up:
    - agent registration keys (agent:*)
    - agent state keys (agent.state:*)
    - agent delivery streams (agent.stream:*)
    - gateway streams (mas.gateway.*)

    This runs before the redis fixture cleanup, so it's safe for tests
    that use the redis fixture.
    """
    redis = Redis.from_url("redis://localhost:6379", decode_responses=True)

    # Collect all keys/streams to delete
    keys_to_delete = []

    # Agent registration and heartbeat keys
    async for key in redis.scan_iter("agent:*"):
        keys_to_delete.append(key)

    # Agent state keys
    async for key in redis.scan_iter("agent.state:*"):
        keys_to_delete.append(key)

    # Agent delivery streams (including instance-specific streams)
    async for key in redis.scan_iter("agent.stream:*"):
        keys_to_delete.append(key)

    # Gateway streams (ingress, dlq, etc.)
    async for key in redis.scan_iter("mas.gateway.*"):
        keys_to_delete.append(key)

    # DLQ streams
    async for key in redis.scan_iter("dlq:*"):
        keys_to_delete.append(key)

    # Rate limit keys
    async for key in redis.scan_iter("rate_limit:*"):
        keys_to_delete.append(key)
    async for key in redis.scan_iter("ratelimit:*"):
        keys_to_delete.append(key)

    # ACL keys
    async for key in redis.scan_iter("acl:*"):
        keys_to_delete.append(key)

    # Audit keys
    async for key in redis.scan_iter("audit:*"):
        keys_to_delete.append(key)

    # Circuit breaker keys
    async for key in redis.scan_iter("circuit:*"):
        keys_to_delete.append(key)

    if keys_to_delete:
        await redis.delete(*keys_to_delete)

    await redis.aclose()  # type: ignore[unresolved-attribute]
    yield


@pytest.fixture
def test_tls(tmp_path_factory) -> TestTlsPaths:
    base_dir = tmp_path_factory.mktemp("certs")

    ca_key = base_dir / "ca.key"
    ca_pem = base_dir / "ca.pem"

    server_key = base_dir / "server.key"
    server_csr = base_dir / "server.csr"
    server_cert = base_dir / "server.pem"
    server_conf = base_dir / "server.cnf"

    _run_openssl(["genrsa", "-out", str(ca_key), "2048"], cwd=base_dir)
    _run_openssl(
        [
            "req",
            "-x509",
            "-new",
            "-nodes",
            "-key",
            str(ca_key),
            "-sha256",
            "-days",
            "3650",
            "-subj",
            "/CN=MAS Test CA",
            "-out",
            str(ca_pem),
        ],
        cwd=base_dir,
    )

    _write_text(
        server_conf,
        textwrap.dedent(
            """
            [req]
            distinguished_name = dn
            req_extensions = req_ext
            prompt = no

            [dn]
            CN = localhost

            [req_ext]
            keyUsage = critical, digitalSignature, keyEncipherment
            extendedKeyUsage = serverAuth
            subjectAltName = @alt_names

            [alt_names]
            DNS.1 = localhost
            IP.1 = 127.0.0.1
            """
        ).lstrip(),
    )

    _run_openssl(["genrsa", "-out", str(server_key), "2048"], cwd=base_dir)
    _run_openssl(
        [
            "req",
            "-new",
            "-key",
            str(server_key),
            "-out",
            str(server_csr),
            "-config",
            str(server_conf),
        ],
        cwd=base_dir,
    )
    _run_openssl(
        [
            "x509",
            "-req",
            "-in",
            str(server_csr),
            "-CA",
            str(ca_pem),
            "-CAkey",
            str(ca_key),
            "-CAcreateserial",
            "-out",
            str(server_cert),
            "-days",
            "3650",
            "-sha256",
            "-extensions",
            "req_ext",
            "-extfile",
            str(server_conf),
        ],
        cwd=base_dir,
    )

    tls = TestTlsPaths(
        base_dir=base_dir,
        ca_pem=str(ca_pem),
        ca_key=str(ca_key),
        server_cert=str(server_cert),
        server_key=str(server_key),
    )

    # Pre-generate the certs used across the test suite.
    for agent_id in [
        "sender",
        "worker",
        "requester",
        "responder",
        "discoverer",
    ]:
        tls._ensure_agent_cert(agent_id)

    return tls


@pytest.fixture
async def mas_server_factory(
    test_tls: TestTlsPaths,
) -> AsyncGenerator[
    Callable[[dict[str, AgentDefinition] | None], Awaitable[MASServer]],
    None,
]:
    servers: list[MASServer] = []

    async def _start(agents: dict[str, AgentDefinition] | None = None) -> MASServer:
        agent_defs = agents or {}
        settings = MASServerSettings(
            redis_url="redis://localhost:6379",
            listen_addr="127.0.0.1:0",
            tls=TlsConfig(
                server_cert_path=test_tls.server_cert,
                server_key_path=test_tls.server_key,
                client_ca_path=test_tls.ca_pem,
            ),
            agents=agent_defs,
        )
        gateway = GatewaySettings(redis=RedisSettings(url="redis://localhost:6379"))
        server = MASServer(settings=settings, gateway=gateway)
        await server.start()
        servers.append(server)
        return server

    yield _start

    for server in servers:
        await server.stop()

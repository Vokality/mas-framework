"""Tests for mas_server.dev local bootstrap helpers."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from mas_agent import Agent, TlsClientConfig
from mas_server import AgentDefinition
from mas_server.dev import (
    client_tls,
    generate_dev_tls,
    start_dev_server,
    validate_dev_listen_addr,
)


def _cert_text(path: str) -> str:
    proc = subprocess.run(
        ["openssl", "x509", "-in", path, "-text", "-noout"],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout


def test_validate_dev_listen_addr_rejects_non_loopback() -> None:
    with pytest.raises(ValueError, match=r"localhost and 127\.0\.0\.1 only"):
        validate_dev_listen_addr("0.0.0.0:50051")


def test_validate_dev_listen_addr_rejects_ipv6_loopback() -> None:
    with pytest.raises(ValueError, match=r"localhost and 127\.0\.0\.1 only"):
        validate_dev_listen_addr("[::1]:50051")


def test_generate_dev_tls_creates_spiffe_sans(tmp_path: Path) -> None:
    bundle = generate_dev_tls(tmp_path, agent_ids=["alpha", "beta"])

    assert bundle.agent_ids == frozenset({"alpha", "beta"})
    assert (tmp_path / "server.pem").exists()
    assert "spiffe://mas/agent/alpha" in _cert_text(str(tmp_path / "alpha.pem"))


def test_client_tls_unknown_agent_raises(tmp_path: Path) -> None:
    bundle = generate_dev_tls(tmp_path, agent_ids=["alpha"])

    with pytest.raises(KeyError, match="unknown dev agent_id"):
        client_tls(bundle, "missing")


@pytest.mark.asyncio
async def test_start_dev_server_agent_ids_must_cover_allowlist(
    tmp_path: Path,
) -> None:
    agents = {
        "sender": AgentDefinition(agent_id="sender", capabilities=[], metadata={}),
        "worker": AgentDefinition(agent_id="worker", capabilities=[], metadata={}),
    }

    with pytest.raises(ValueError, match="missing: worker"):
        await start_dev_server(
            agents=agents,
            cert_dir=tmp_path,
            agent_ids=["sender"],
        )


@pytest.mark.asyncio
async def test_start_dev_server_rejects_empty_agents(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one allowlisted agent"):
        await start_dev_server(agents={}, cert_dir=tmp_path)


@pytest.mark.asyncio
async def test_start_dev_server_mesh_false_leaves_acl_deny_by_default(
    redis,
    tmp_path: Path,
) -> None:
    agents = {
        "sender": AgentDefinition(agent_id="sender", capabilities=[], metadata={}),
        "worker": AgentDefinition(agent_id="worker", capabilities=[], metadata={}),
    }
    server, _bundle = await start_dev_server(
        agents=agents,
        listen_addr="127.0.0.1:0",
        cert_dir=tmp_path,
        mesh=False,
    )
    try:
        allowed = await redis.smembers("agent:sender:allowed_targets")
        assert allowed == set()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_start_dev_server_mesh_true_allows_peer_messaging(
    tmp_path: Path,
) -> None:
    agents = {
        "sender": AgentDefinition(agent_id="sender", capabilities=[], metadata={}),
        "worker": AgentDefinition(agent_id="worker", capabilities=[], metadata={}),
    }
    server, tls = await start_dev_server(
        agents=agents,
        listen_addr="127.0.0.1:0",
        cert_dir=tmp_path,
        mesh=True,
    )

    received = asyncio.Event()

    class Worker(Agent):
        async def on_message(self, message) -> None:
            del message
            received.set()

    sender_creds = client_tls(tls, "sender")
    worker_creds = client_tls(tls, "worker")
    sender = Agent(
        "sender",
        server_addr=server.bound_addr,
        tls=TlsClientConfig(
            root_ca_path=sender_creds.root_ca_path,
            client_cert_path=sender_creds.client_cert_path,
            client_key_path=sender_creds.client_key_path,
        ),
    )
    worker = Worker(
        "worker",
        server_addr=server.bound_addr,
        tls=TlsClientConfig(
            root_ca_path=worker_creds.root_ca_path,
            client_cert_path=worker_creds.client_cert_path,
            client_key_path=worker_creds.client_key_path,
        ),
    )

    await worker.start()
    await sender.start()
    try:
        await sender.send("worker", "ping", {})
        await asyncio.wait_for(received.wait(), timeout=2.0)
    finally:
        await sender.stop()
        await worker.stop()
        await server.stop()

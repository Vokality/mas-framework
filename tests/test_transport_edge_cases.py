from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mas import Agent
from mas.agent import TlsClientConfig
from mas.gateway.config import GatewaySettings, RedisSettings
from mas.server import AgentDefinition, MASServer, MASServerSettings, TlsConfig

from tests.conftest import _run_openssl, _write_text

pytestmark = pytest.mark.asyncio


async def _wait_until(predicate, *, timeout: float = 2.0) -> None:
    start = asyncio.get_running_loop().time()
    while True:
        if predicate():
            return
        if asyncio.get_running_loop().time() - start > timeout:
            raise AssertionError("timeout")
        await asyncio.sleep(0.02)


def _create_client_cert_without_spiffe(
    base_dir: Path, *, ca_pem: str, ca_key: str, name: str
) -> TlsClientConfig:
    key_path = base_dir / f"{name}.key"
    csr_path = base_dir / f"{name}.csr"
    cert_path = base_dir / f"{name}.pem"
    conf_path = base_dir / f"{name}.cnf"

    _write_text(
        conf_path,
        """
        [req]
        distinguished_name = dn
        req_extensions = req_ext
        prompt = no

        [dn]
        CN = invalid-client

        [req_ext]
        keyUsage = critical, digitalSignature, keyEncipherment
        extendedKeyUsage = clientAuth
        subjectAltName = @alt_names

        [alt_names]
        DNS.1 = localhost
        """.lstrip(),
    )

    _run_openssl(["genrsa", "-out", str(key_path), "2048"], cwd=base_dir)
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
        cwd=base_dir,
    )
    _run_openssl(
        [
            "x509",
            "-req",
            "-in",
            str(csr_path),
            "-CA",
            ca_pem,
            "-CAkey",
            ca_key,
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
        cwd=base_dir,
    )

    return TlsClientConfig(
        root_ca_path=ca_pem,
        client_cert_path=str(cert_path),
        client_key_path=str(key_path),
    )


async def test_reclaim_pending_after_instance_disconnect(test_tls) -> None:
    agents = {
        "sender": AgentDefinition(agent_id="sender", capabilities=[], metadata={}),
        "worker": AgentDefinition(agent_id="worker", capabilities=[], metadata={}),
    }
    settings = MASServerSettings(
        redis_url="redis://localhost:6379",
        listen_addr="127.0.0.1:0",
        tls=TlsConfig(
            server_cert_path=test_tls.server_cert,
            server_key_path=test_tls.server_key,
            client_ca_path=test_tls.ca_pem,
        ),
        agents=agents,
        reclaim_idle_ms=200,
        reclaim_batch_size=10,
    )
    gateway = GatewaySettings(redis=RedisSettings(url="redis://localhost:6379"))
    server = MASServer(settings=settings, gateway=gateway)
    await server.start()
    await server.authz.set_permissions("sender", allowed_targets=["worker"])

    class BlockingWorker(Agent):
        def __init__(self, agent_id: str, **kwargs) -> None:
            super().__init__(agent_id, **kwargs)
            self.received = asyncio.Event()

        async def on_message(self, message) -> None:
            self.received.set()
            await asyncio.Event().wait()

    class RecordingWorker(Agent):
        def __init__(self, agent_id: str, **kwargs) -> None:
            super().__init__(agent_id, **kwargs)
            self.received = asyncio.Event()

        async def on_message(self, message) -> None:
            self.received.set()

    worker1 = BlockingWorker(
        "worker",
        server_addr=server.bound_addr,
        tls=test_tls.client("worker"),
    )
    sender = Agent(
        "sender",
        server_addr=server.bound_addr,
        tls=test_tls.client("sender"),
    )

    await worker1.start()
    await sender.start()
    try:
        await sender.send("worker", "work", {"x": 1})
        await worker1.received.wait()
    finally:
        await worker1.stop()

    worker2 = RecordingWorker(
        "worker",
        server_addr=server.bound_addr,
        tls=test_tls.client("worker"),
    )
    await worker2.start()
    try:
        await _wait_until(worker2.received.is_set, timeout=2.0)
    finally:
        await worker2.stop()
        await sender.stop()
        await server.stop()


async def test_transport_rejects_missing_spiffe_san(test_tls, tmp_path) -> None:
    agents = {
        "worker": AgentDefinition(agent_id="worker", capabilities=[], metadata={})
    }
    settings = MASServerSettings(
        redis_url="redis://localhost:6379",
        listen_addr="127.0.0.1:0",
        tls=TlsConfig(
            server_cert_path=test_tls.server_cert,
            server_key_path=test_tls.server_key,
            client_ca_path=test_tls.ca_pem,
        ),
        agents=agents,
    )
    gateway = GatewaySettings(redis=RedisSettings(url="redis://localhost:6379"))
    server = MASServer(settings=settings, gateway=gateway)
    await server.start()

    class FastTimeoutAgent(Agent):
        async def wait_transport_ready(self, timeout: float | None = None) -> None:
            await super().wait_transport_ready(timeout=0.5)

    tls = _create_client_cert_without_spiffe(
        tmp_path, ca_pem=test_tls.ca_pem, ca_key=test_tls.ca_key, name="invalid"
    )
    agent = FastTimeoutAgent("worker", server_addr=server.bound_addr, tls=tls)

    try:
        with pytest.raises(asyncio.TimeoutError):
            await agent.start()
    finally:
        await agent.stop()
        await server.stop()

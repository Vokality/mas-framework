"""Real file rotation and current channel trust/revocation enforcement."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import grpc
import grpc.aio as grpc_aio
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.x509.verification import VerificationError
from mas_gateway.audit import AuditModule
from mas_gateway.config import GatewaySettings
from mas_proto.runtime.v1 import runtime_pb2 as mas_pb2
from mas_proto.runtime.v1 import runtime_pb2_grpc as mas_pb2_grpc
from mas_server.dev import generate_dev_tls
from mas_server.runtime import MASServer
from mas_server.tls import PeerCertificatePolicy, load_server_credentials
from mas_server.types import AgentDefinition, MASServerSettings, TlsConfig
from redis.asyncio import Redis

from conftest import TestTlsPaths as TlsPaths


def test_peer_revocation_reloads_after_atomic_file_change(
    test_tls: TlsPaths, tmp_path: Path
) -> None:
    manifest = tmp_path / "revoked.json"
    manifest.write_text("[]")
    tls = TlsConfig(
        test_tls.server_cert,
        test_tls.server_key,
        test_tls.ca_pem,
        revoked_certificates_path=str(manifest),
    )
    policy = PeerCertificatePolicy(tls)
    leaf = Path(test_tls.client("worker").client_cert_path).read_bytes()
    policy.validate(leaf, b"")
    fingerprint = (
        x509.load_pem_x509_certificate(leaf).fingerprint(hashes.SHA256()).hex()
    )
    replacement = manifest.with_suffix(".new")
    replacement.write_text(json.dumps([fingerprint]))
    replacement.replace(manifest)
    with pytest.raises(ValueError, match="revoked"):
        policy.validate(leaf, b"")
    manifest.write_text("broken-json")
    with pytest.raises((ValueError, VerificationError)):
        policy.validate(leaf, b"")


def test_removing_ca_trust_rejects_existing_peer(
    test_tls: TlsPaths, tmp_path: Path
) -> None:
    trust = tmp_path / "trust.pem"
    trust.write_bytes(Path(test_tls.ca_pem).read_bytes())
    tls = TlsConfig(test_tls.server_cert, test_tls.server_key, str(trust))
    policy = PeerCertificatePolicy(tls)
    leaf = Path(test_tls.client("worker").client_cert_path).read_bytes()
    policy.validate(leaf, b"")
    other = generate_dev_tls(tmp_path / "other", agent_ids=frozenset({"other"}))
    trust.write_bytes(Path(other.ca_pem).read_bytes())
    with pytest.raises((ValueError, VerificationError)):
        policy.validate(leaf, b"")


def test_dynamic_credentials_reload_only_valid_rotations(
    test_tls: TlsPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tls = TlsConfig(test_tls.server_cert, test_tls.server_key, test_tls.ca_pem)
    callbacks: list[object] = []
    original = grpc.dynamic_ssl_server_credentials

    def capture(
        initial: grpc.ServerCertificateConfiguration,
        fetcher: object,
        require_client_authentication: bool = False,
    ) -> grpc.ServerCredentials:
        callbacks.append(fetcher)
        return original(initial, fetcher, require_client_authentication)

    monkeypatch.setattr(grpc, "dynamic_ssl_server_credentials", capture)
    assert load_server_credentials(tls) is not None
    fetcher = callbacks[0]
    assert callable(fetcher)
    assert fetcher() is None
    other = generate_dev_tls(tmp_path / "other", agent_ids=frozenset({"other"}))
    original_cert = Path(test_tls.server_cert).read_bytes()
    original_key = Path(test_tls.server_key).read_bytes()
    try:
        Path(test_tls.server_cert).write_bytes(Path(other.server_cert).read_bytes())
        assert fetcher() is None
        Path(test_tls.server_key).write_bytes(Path(other.server_key).read_bytes())
        assert fetcher() is not None
        assert fetcher() is None
    finally:
        Path(test_tls.server_cert).write_bytes(original_cert)
        Path(test_tls.server_key).write_bytes(original_key)
    with pytest.raises(ValueError, match="do not match"):
        load_server_credentials(replace(tls, server_key_path=other.server_key))


async def test_revoked_certificate_is_denied_on_an_existing_grpc_channel(
    redis: Redis, test_tls: TlsPaths, tmp_path: Path
) -> None:
    manifest = tmp_path / "revoked.json"
    manifest.write_text("[]")
    tls = TlsConfig(
        test_tls.server_cert,
        test_tls.server_key,
        test_tls.ca_pem,
        revoked_certificates_path=str(manifest),
    )
    server = MASServer(
        settings=MASServerSettings(
            listen_addr="127.0.0.1:0",
            tls=tls,
            agents={"worker": AgentDefinition("worker", [], {})},
        ),
        gateway=GatewaySettings(),
    )
    client = test_tls.client("worker")
    leaf = Path(client.client_cert_path).read_bytes()
    credentials = grpc.ssl_channel_credentials(
        root_certificates=Path(test_tls.ca_pem).read_bytes(),
        private_key=Path(client.client_key_path).read_bytes(),
        certificate_chain=leaf,
    )
    await server.start()
    try:
        async with grpc_aio.secure_channel(server.bound_addr, credentials) as channel:
            stub = mas_pb2_grpc.RuntimeServiceStub(channel)
            await stub.Discover(mas_pb2.DiscoverRequest(), timeout=2)
            fingerprint = (
                x509.load_pem_x509_certificate(leaf).fingerprint(hashes.SHA256()).hex()
            )
            replacement = manifest.with_suffix(".new")
            replacement.write_text(json.dumps([fingerprint]))
            replacement.replace(manifest)
            with pytest.raises(grpc_aio.AioRpcError) as failure:
                await stub.Discover(mas_pb2.DiscoverRequest(), timeout=2)
            assert failure.value.code() == grpc.StatusCode.UNAUTHENTICATED
            assert failure.value.details() == "peer_certificate_rejected"
        events = await AuditModule(redis, file_sink=None).query_security_events()
        assert events[-1]["event_type"] == "AUTHENTICATION_DENIED"
        assert events[-1]["details"] == {"reason": "peer_certificate_rejected"}
        assert fingerprint not in json.dumps(events)
    finally:
        await server.stop()

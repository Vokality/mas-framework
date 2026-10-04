"""Peer authentication keeps fresh security checks off the event loop."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import grpc
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from mas_server.authn import spiffe_agent_id
from mas_server.dev import generate_dev_tls
from mas_server.errors import RpcError, UnauthenticatedError
from mas_server.peer_validation import PeerValidationCapacityError, QueuedPeerValidator
from mas_server.tls import PeerCertificatePolicy
from mas_server.types import TlsConfig

pytestmark = pytest.mark.asyncio


@dataclass(slots=True)
class PeerContext:
    properties: dict[str, Iterable[bytes]] = field(default_factory=dict)
    accessed_from: list[int] = field(default_factory=list)

    def auth_context(self) -> Mapping[str, Iterable[bytes]]:
        self.accessed_from.append(threading.get_ident())
        return self.properties


async def test_fresh_peer_validation_keeps_event_loop_responsive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    released = threading.Event()
    stopped = asyncio.Event()
    threads: list[int] = []
    ticks = 0
    loop_thread = threading.get_ident()
    context = PeerContext(
        {
            "x509_pem_cert": (b"peer",),
            "x509_pem_cert_chain": (b"chain",),
            "x509_subject_alternative_name": (b"spiffe://mas/agent/worker",),
        }
    )
    tls = TlsConfig("unused.pem", "unused.key", "unused-ca.pem")

    def validate(policy: PeerCertificatePolicy, leaf: bytes, chain: bytes) -> None:
        assert (leaf, chain) == (b"peer", b"chain")
        threads.append(threading.get_ident())
        started.set()
        if not released.wait(1):
            raise TimeoutError("test validation did not finish")

    async def heartbeat() -> None:
        nonlocal ticks
        while not stopped.is_set():
            ticks += 1
            await asyncio.sleep(0)

    monkeypatch.setattr(PeerCertificatePolicy, "validate", validate)
    heartbeat_task = asyncio.create_task(heartbeat())
    auth_task = asyncio.create_task(spiffe_agent_id(context, tls=tls))
    try:
        assert await asyncio.to_thread(started.wait, 0.5)
        before = ticks
        await asyncio.sleep(0.005)
        assert ticks > before
        assert not auth_task.done()
        released.set()
        assert await auth_task == "worker"
        assert await spiffe_agent_id(context, tls=tls) == "worker"
        assert len(threads) == 2
        assert all(thread != loop_thread for thread in threads)
        assert context.accessed_from == [loop_thread, loop_thread]
    finally:
        released.set()
        stopped.set()
        await asyncio.gather(auth_task, heartbeat_task, return_exceptions=True)


async def test_documented_iterable_certificate_properties_are_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validated: list[tuple[bytes, bytes]] = []

    def validate(policy: PeerCertificatePolicy, leaf: bytes, chain: bytes) -> None:
        validated.append((leaf, chain))

    monkeypatch.setattr(PeerCertificatePolicy, "validate", validate)
    context = PeerContext(
        {
            "x509_pem_cert": iter([b"peer"]),
            "x509_pem_cert_chain": iter([b"chain1", b"chain2"]),
            "x509_subject_alternative_name": iter([b"spiffe://mas/agent/worker"]),
        }
    )
    tls = TlsConfig("unused.pem", "unused.key", "unused-ca.pem")
    assert await spiffe_agent_id(context, tls=tls) == "worker"
    assert validated == [(b"peer", b"chain1chain2")]


@pytest.mark.parametrize("error_type", [OSError, ValueError])
async def test_worker_validation_errors_preserve_fail_closed_status(
    monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
) -> None:
    def validate(policy: PeerCertificatePolicy, leaf: bytes, chain: bytes) -> None:
        raise error_type("private credential path or certificate detail")

    monkeypatch.setattr(PeerCertificatePolicy, "validate", validate)
    context = PeerContext(
        {
            "x509_pem_cert": (b"peer",),
            "x509_subject_alternative_name": (b"spiffe://mas/agent/worker",),
        }
    )
    with pytest.raises(UnauthenticatedError) as failure:
        await spiffe_agent_id(
            context, tls=TlsConfig("unused.pem", "unused.key", "unused-ca.pem")
        )
    assert failure.value.message == "peer_certificate_rejected"


async def test_atomic_revocation_is_checked_again_on_next_authentication(
    tmp_path: Path,
) -> None:
    bundle = await asyncio.to_thread(
        generate_dev_tls, tmp_path / "tls", agent_ids=frozenset({"worker"})
    )
    revoked = tmp_path / "revoked.json"
    revoked.write_text("[]")
    leaf = Path(bundle.client("worker").client_cert_path).read_bytes()
    context = PeerContext(
        {
            "x509_pem_cert": (leaf,),
            "x509_subject_alternative_name": (b"spiffe://mas/agent/worker",),
        }
    )
    tls = TlsConfig(
        bundle.server_cert,
        bundle.server_key,
        bundle.ca_pem,
        revoked_certificates_path=str(revoked),
    )
    assert await spiffe_agent_id(context, tls=tls) == "worker"
    fingerprint = (
        x509.load_pem_x509_certificate(leaf).fingerprint(hashes.SHA256()).hex()
    )
    replacement = revoked.with_suffix(".new")
    replacement.write_text(json.dumps([fingerprint]))
    replacement.replace(revoked)
    with pytest.raises(UnauthenticatedError, match="peer_certificate_rejected"):
        await spiffe_agent_id(context, tls=tls)
    revoked.write_text("broken-json")
    with pytest.raises(UnauthenticatedError, match="peer_certificate_rejected"):
        await spiffe_agent_id(context, tls=tls)


async def test_validator_backpressure_is_not_reported_as_invalid_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def validate(
        validator: QueuedPeerValidator, leaf: bytes, chain: bytes
    ) -> None:
        raise PeerValidationCapacityError("private queue state")

    monkeypatch.setattr(QueuedPeerValidator, "validate", validate)
    context = PeerContext(
        {
            "x509_pem_cert": (b"peer",),
            "x509_subject_alternative_name": (b"spiffe://mas/agent/worker",),
        }
    )
    with pytest.raises(RpcError) as failure:
        await spiffe_agent_id(
            context, tls=TlsConfig("unused.pem", "unused.key", "unused-ca.pem")
        )
    assert failure.value.status is grpc.StatusCode.RESOURCE_EXHAUSTED
    assert failure.value.message == "peer_validation_capacity"


@pytest.mark.parametrize(
    ("properties", "reason"),
    [
        ({}, "missing_spiffe_san"),
        ({"x509_subject_alternative_name": (b"not-spiffe",)}, "invalid_spiffe_san"),
        (
            {"x509_subject_alternative_name": (b"spiffe://other/agent/worker",)},
            "invalid_spiffe_format",
        ),
        (
            {
                "x509_subject_alternative_name": (
                    b"spiffe://mas/agent/worker",
                    b"spiffe://mas/agent/other",
                )
            },
            "invalid_spiffe_san",
        ),
        ({"x509_subject_alternative_name": (b"\xff",)}, "invalid_spiffe_san"),
    ],
)
async def test_existing_san_validation_contract_is_preserved(
    properties: dict[str, Iterable[bytes]], reason: str
) -> None:
    with pytest.raises(UnauthenticatedError) as failure:
        await spiffe_agent_id(PeerContext(properties))
    assert failure.value.message == reason

"""Verified peers skip PEM parsing while current policy and expiry stay enforced."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from cryptography.x509.verification import VerificationError
from mas_server import tls as tls_module
from mas_server.tls import PeerCertificatePolicy
from mas_server.types import TlsConfig


@dataclass(frozen=True)
class PeerMaterial:
    """One isolated peer identity and atomically replaceable policy files."""

    policy: PeerCertificatePolicy
    leaf_pem: bytes
    leaf: x509.Certificate
    root: x509.Certificate
    trust: Path
    revoked: Path


def _material(directory: Path, *, root_expires_first: bool = False) -> PeerMaterial:
    directory.mkdir()
    now = datetime.now(UTC).replace(microsecond=0)
    root_key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "cache-test-root")])
    root = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(minutes=1 if root_expires_first else 10))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(root_key, hashes.SHA256())
    )
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "worker")]))
        .issuer_name(name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(minutes=5))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.UniformResourceIdentifier("spiffe://agent/worker")]
            ),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False
        )
        .sign(root_key, hashes.SHA256())
    )
    trust, revoked = directory / "trust.pem", directory / "revoked.json"
    trust.write_bytes(root.public_bytes(serialization.Encoding.PEM))
    revoked.write_text("[]")
    return PeerMaterial(
        PeerCertificatePolicy(
            TlsConfig("unused-cert", "unused-key", str(trust), str(revoked))
        ),
        leaf.public_bytes(serialization.Encoding.PEM),
        leaf,
        root,
        trust,
        revoked,
    )


def _count_leaf_parsing(monkeypatch: pytest.MonkeyPatch) -> list[bytes]:
    parsed: list[bytes] = []
    original = x509.load_pem_x509_certificate

    def parse(data: bytes) -> x509.Certificate:
        parsed.append(data)
        return original(data)

    monkeypatch.setattr(tls_module.x509, "load_pem_x509_certificate", parse)
    return parsed


def test_unchanged_valid_peer_parses_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    material = _material(tmp_path / "peer")
    parsed = _count_leaf_parsing(monkeypatch)
    for _ in range(100):
        material.policy.validate(material.leaf_pem, b"")
    assert parsed == [material.leaf_pem]
    assert list(material.policy._verified) == [
        hashlib.sha256(material.leaf_pem).hexdigest()
    ]


def test_atomic_revocation_update_rejects_cached_peer_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    material = _material(tmp_path / "peer")
    parsed = _count_leaf_parsing(monkeypatch)
    material.policy.validate(material.leaf_pem, b"")
    replacement = material.revoked.with_suffix(".new")
    replacement.write_text(
        json.dumps([material.leaf.fingerprint(hashes.SHA256()).hex()])
    )
    replacement.replace(material.revoked)
    with pytest.raises(ValueError, match="revoked"):
        material.policy.validate(material.leaf_pem, b"")
    assert parsed == [material.leaf_pem, material.leaf_pem]
    assert not material.policy._verified


def test_atomic_trust_update_rejects_cached_peer_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    material, other = _material(tmp_path / "peer"), _material(tmp_path / "other")
    parsed = _count_leaf_parsing(monkeypatch)
    material.policy.validate(material.leaf_pem, b"")
    replacement = material.trust.with_suffix(".new")
    replacement.write_bytes(other.trust.read_bytes())
    replacement.replace(material.trust)
    with pytest.raises(VerificationError):
        material.policy.validate(material.leaf_pem, b"")
    assert parsed == [material.leaf_pem, material.leaf_pem]
    assert not material.policy._verified


@pytest.mark.parametrize("root_expires_first", [False, True])
def test_leaf_or_trust_expiry_misses_cache_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, root_expires_first: bool
) -> None:
    material = _material(tmp_path / "peer", root_expires_first=root_expires_first)
    parsed = _count_leaf_parsing(monkeypatch)
    material.policy.validate(material.leaf_pem, b"")
    expired_at = min(
        material.leaf.not_valid_after_utc, material.root.not_valid_after_utc
    ) + timedelta(seconds=1)

    class Clock:
        @staticmethod
        def now(tz: tzinfo | None = None) -> datetime:
            return expired_at

    monkeypatch.setattr(tls_module, "datetime", Clock)
    with pytest.raises(VerificationError):
        material.policy.validate(material.leaf_pem, b"")
    assert parsed == [material.leaf_pem, material.leaf_pem]


def test_verified_peer_cache_keeps_existing_capacity_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    material = _material(tmp_path / "peer")
    parsed = _count_leaf_parsing(monkeypatch)
    material.policy.validate(material.leaf_pem, b"")
    material.policy._verified = {
        f"other-{index}": material.leaf.not_valid_after_utc for index in range(2048)
    }
    material.policy.validate(material.leaf_pem, b"")
    material.policy.validate(material.leaf_pem, b"")
    assert len(material.policy._verified) == 1
    assert parsed == [material.leaf_pem, material.leaf_pem]

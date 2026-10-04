"""Reloadable TLS identities and current client trust enforcement."""

from __future__ import annotations

import hashlib
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import grpc
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import ExtendedKeyUsageOID
from cryptography.x509.verification import (
    Criticality,
    ExtensionPolicy,
    PolicyBuilder,
    Store,
)
from pydantic import TypeAdapter

if TYPE_CHECKING:
    from .types import TlsConfig

logger = logging.getLogger(__name__)
_FINGERPRINTS = TypeAdapter(list[str])


def _read_bounded(path: str, limit: int = 1_048_576) -> bytes:
    with open(path, "rb") as source:
        data = source.read(limit + 1)
    if len(data) > limit:
        raise ValueError("TLS credential file exceeds bound")
    return data


def _read_identity(tls: TlsConfig) -> tuple[bytes, bytes, bytes]:
    cert = _read_bounded(tls.server_cert_path)
    key = _read_bounded(tls.server_key_path, 65536)
    ca = _read_bounded(tls.client_ca_path)
    certificates = x509.load_pem_x509_certificates(cert)
    if not certificates:
        raise ValueError("Server certificate chain is empty")
    leaf = certificates[0]
    now = datetime.now(UTC)
    if not leaf.not_valid_before_utc <= now < leaf.not_valid_after_utc:
        raise ValueError("Server certificate is outside its validity interval")
    private = serialization.load_pem_private_key(key, password=None)
    if private.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    ) != leaf.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    ):
        raise ValueError("Server certificate and key do not match")
    roots = x509.load_pem_x509_certificates(ca)
    if not roots:
        raise ValueError("Client trust bundle is empty")
    for root in roots:
        if not root.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
            raise ValueError("Client trust bundle contains a non-CA certificate")
        if not root.not_valid_before_utc <= now < root.not_valid_after_utc:
            raise ValueError(
                "Client trust certificate is outside its validity interval"
            )
    return cert, key, ca


def load_server_credentials(tls: TlsConfig) -> grpc.ServerCredentials:
    """Reload a valid certificate bundle before each new gRPC handshake.

    A malformed rotation keeps the last valid handshake configuration. Application
    authentication independently enforces the current trust and revocation files.
    Rotate a versioned credential directory with an atomic symlink replacement.
    """
    cert, key, ca = _read_identity(tls)
    current = (cert, key, ca)
    initial = grpc.ssl_server_certificate_configuration(
        [(key, cert)], root_certificates=ca
    )
    lock = threading.Lock()

    def fetch() -> grpc.ServerCertificateConfiguration | None:
        nonlocal current
        with lock:
            try:
                updated = _read_identity(tls)
            except (OSError, ValueError, x509.ExtensionNotFound):
                logger.error("TLS rotation rejected: invalid credential bundle")
                return None
            if updated == current:
                return None
            new_cert, new_key, new_ca = updated
            configuration = grpc.ssl_server_certificate_configuration(
                [(new_key, new_cert)], root_certificates=new_ca
            )
            current = updated
            return configuration

    return grpc.dynamic_ssl_server_credentials(
        initial, fetch, require_client_authentication=True
    )


class PeerCertificatePolicy:
    """Enforce current trust and revoked fingerprints on existing channels."""

    def __init__(self, tls: TlsConfig) -> None:
        self._tls = tls
        self._signature: tuple[tuple[int, int, int], ...] | None = None
        self._roots: list[x509.Certificate] = []
        self._revoked: frozenset[str] = frozenset()
        self._verified: dict[str, datetime] = {}
        self._lock = threading.Lock()

    def validate(self, leaf_pem: bytes, chain_pem: bytes) -> None:
        """Fail closed on malformed policy, expiry, trust removal or revocation."""
        if len(leaf_pem) > 65536 or len(chain_pem) > 1_048_576:
            raise ValueError("Peer certificate exceeds bound")
        with self._lock:
            paths = [self._tls.client_ca_path]
            if self._tls.revoked_certificates_path is not None:
                paths.append(self._tls.revoked_certificates_path)
            signature = tuple(
                (stat.st_ino, stat.st_mtime_ns, stat.st_size)
                for stat in (Path(path).stat() for path in paths)
            )
            if signature != self._signature:
                roots = x509.load_pem_x509_certificates(
                    _read_bounded(self._tls.client_ca_path)
                )
                if not roots:
                    raise ValueError("Client trust bundle is empty")
                revoked: list[str] = []
                if self._tls.revoked_certificates_path is not None:
                    revoked = _FINGERPRINTS.validate_json(
                        _read_bounded(self._tls.revoked_certificates_path, 1_048_576),
                        strict=True,
                    )
                    if any(
                        len(value) != 64
                        or any(char not in "0123456789abcdefABCDEF" for char in value)
                        for value in revoked
                    ):
                        raise ValueError("Invalid revoked certificate fingerprint")
                self._roots = roots
                self._revoked = frozenset(value.lower() for value in revoked)
                self._signature = signature
                self._verified.clear()
            now = datetime.now(UTC)
            pem_digest = hashlib.sha256(leaf_pem).hexdigest()
            valid_until = self._verified.get(pem_digest)
            if valid_until is not None and now < valid_until:
                return
            leaf = x509.load_pem_x509_certificate(leaf_pem)
            fingerprint = leaf.fingerprint(hashes.SHA256()).hex()
            if fingerprint in self._revoked:
                raise ValueError("Peer certificate revoked")
            # SPIFFE URI SANs are application identities, not WebPKI DNS names.
            verifier = (
                PolicyBuilder()
                .store(Store(self._roots))
                .time(now)
                .max_chain_depth(8)
                .extension_policies(
                    ca_policy=ExtensionPolicy.permit_all().require_present(
                        x509.BasicConstraints, Criticality.CRITICAL, None
                    ),
                    ee_policy=ExtensionPolicy.permit_all(),
                )
                .build_client_verifier()
            )
            intermediates = (
                x509.load_pem_x509_certificates(chain_pem) if chain_pem else []
            )
            result = verifier.verify(leaf, intermediates)
            usage = leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
            if ExtendedKeyUsageOID.CLIENT_AUTH not in usage:
                raise ValueError("Peer certificate lacks client authentication usage")
            if len(self._verified) >= 2048:
                self._verified.clear()
            self._verified[pem_digest] = min(
                cert.not_valid_after_utc for cert in result.chain
            )

"""TLS credential loading for server runtime."""

from __future__ import annotations

import grpc

from .types import TlsConfig


def load_server_credentials(tls: TlsConfig) -> grpc.ServerCredentials:
    """Load TLS credentials for the gRPC server."""
    with open(tls.server_cert_path, "rb") as f:
        server_cert = f.read()
    with open(tls.server_key_path, "rb") as f:
        server_key = f.read()
    with open(tls.client_ca_path, "rb") as f:
        client_ca = f.read()

    return grpc.ssl_server_credentials(
        [(server_key, server_cert)],
        root_certificates=client_ca,
        require_client_auth=True,
    )

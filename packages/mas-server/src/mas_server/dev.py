"""Local development bootstrap helpers.

These utilities generate ephemeral mTLS material and start a broker with
``GatewaySettings`` defaults. They are intended for local development and
tests only — not for production deployments.
"""

from __future__ import annotations

import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

from mas_gateway import GatewaySettings

from .runtime import MASServer
from .types import AgentDefinition, MASServerSettings, TlsConfig

DEFAULT_DEV_AGENT_IDS = frozenset(
    {"sender", "worker", "requester", "responder", "discoverer"}
)


@dataclass(frozen=True, slots=True)
class DevClientTls:
    """Client TLS material paths compatible with ``mas_agent.TlsClientConfig``."""

    root_ca_path: str
    client_cert_path: str
    client_key_path: str


@dataclass(frozen=True, slots=True)
class DevTlsBundle:
    """Filesystem paths for a generated dev/test mTLS bundle."""

    base_dir: Path
    ca_pem: str
    ca_key: str
    server_cert: str
    server_key: str
    agent_ids: frozenset[str]

    def client(self, agent_id: str) -> DevClientTls:
        """Return client TLS paths for a pre-generated agent identity."""
        return client_tls(self, agent_id)


def run_openssl(args: list[str], *, cwd: Path) -> None:
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


def write_text_file(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _parse_listen_addr(listen_addr: str) -> tuple[str, int]:
    if listen_addr.startswith("["):
        host, rest = listen_addr.split("]", 1)
        port_text = rest.lstrip(":")
        if not port_text:
            raise ValueError(f"invalid listen_addr: {listen_addr!r}")
        return host[1:], int(port_text)

    if ":" not in listen_addr:
        raise ValueError(f"invalid listen_addr: {listen_addr!r}")
    host, port_text = listen_addr.rsplit(":", 1)
    return host, int(port_text)


def _dev_listen_host_allowed(host: str) -> bool:
    """Return whether bundled dev server certificates cover ``host``."""
    return host in {"localhost", "127.0.0.1"}


def validate_dev_listen_addr(listen_addr: str) -> None:
    """Ensure ``listen_addr`` matches bundled dev certificate SANs."""
    host, _port = _parse_listen_addr(listen_addr)
    if not _dev_listen_host_allowed(host):
        msg = (
            "dev TLS certificates cover localhost and 127.0.0.1 only "
            f"(got host {host!r} in {listen_addr!r})"
        )
        raise ValueError(msg)


def _resolve_agent_ids(
    agents: dict[str, AgentDefinition],
    agent_ids: list[str] | frozenset[str] | None,
) -> frozenset[str]:
    if agent_ids is not None:
        ids = frozenset(agent_ids)
    elif agents:
        ids = frozenset(agents)
    else:
        ids = DEFAULT_DEV_AGENT_IDS

    missing = frozenset(agents) - ids
    if missing:
        missing_text = ", ".join(sorted(missing))
        raise ValueError(
            f"agent_ids must include every allowlisted agent; missing: {missing_text}"
        )
    return ids


def generate_dev_tls(
    base_dir: Path,
    *,
    agent_ids: list[str] | frozenset[str] | None = None,
) -> DevTlsBundle:
    """Generate a dev CA, server certificate, and agent client certificates.

    Requires the ``openssl`` CLI on ``PATH``. Agent certificates are created
    eagerly for every id in ``agent_ids``.
    """
    base_dir.mkdir(parents=True, exist_ok=True)
    ids = frozenset(agent_ids or DEFAULT_DEV_AGENT_IDS)
    for agent_id in ids:
        if not agent_id:
            raise ValueError("agent_id must be non-empty")

    ca_key = base_dir / "ca.key"
    ca_pem = base_dir / "ca.pem"
    server_key = base_dir / "server.key"
    server_csr = base_dir / "server.csr"
    server_cert = base_dir / "server.pem"
    server_conf = base_dir / "server.cnf"

    run_openssl(["genrsa", "-out", str(ca_key), "2048"], cwd=base_dir)
    run_openssl(
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
            "/CN=MAS Dev CA",
            "-out",
            str(ca_pem),
        ],
        cwd=base_dir,
    )

    write_text_file(
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

    run_openssl(["genrsa", "-out", str(server_key), "2048"], cwd=base_dir)
    run_openssl(
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
    run_openssl(
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

    bundle = DevTlsBundle(
        base_dir=base_dir,
        ca_pem=str(ca_pem),
        ca_key=str(ca_key),
        server_cert=str(server_cert),
        server_key=str(server_key),
        agent_ids=ids,
    )
    for agent_id in sorted(ids):
        _generate_agent_cert(bundle, agent_id)
    return bundle


def _generate_agent_cert(bundle: DevTlsBundle, agent_id: str) -> None:
    cert_path = bundle.base_dir / f"{agent_id}.pem"
    key_path = bundle.base_dir / f"{agent_id}.key"
    csr_path = bundle.base_dir / f"{agent_id}.csr"
    conf_path = bundle.base_dir / f"{agent_id}.cnf"

    write_text_file(
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

    run_openssl(["genrsa", "-out", str(key_path), "2048"], cwd=bundle.base_dir)
    run_openssl(
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
        cwd=bundle.base_dir,
    )
    run_openssl(
        [
            "x509",
            "-req",
            "-in",
            str(csr_path),
            "-CA",
            bundle.ca_pem,
            "-CAkey",
            bundle.ca_key,
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
        cwd=bundle.base_dir,
    )


def client_tls(bundle: DevTlsBundle, agent_id: str) -> DevClientTls:
    """Return TLS client paths for an agent cert generated in ``bundle``."""
    if agent_id not in bundle.agent_ids:
        msg = (
            f"unknown dev agent_id {agent_id!r}; "
            f"expected one of {sorted(bundle.agent_ids)}"
        )
        raise KeyError(msg)

    cert_path = bundle.base_dir / f"{agent_id}.pem"
    key_path = bundle.base_dir / f"{agent_id}.key"
    if not cert_path.exists() or not key_path.exists():
        raise FileNotFoundError(f"missing dev TLS material for agent {agent_id!r}")

    return DevClientTls(
        root_ca_path=bundle.ca_pem,
        client_cert_path=str(cert_path),
        client_key_path=str(key_path),
    )


def dev_server_settings(
    *,
    agents: dict[str, AgentDefinition],
    tls: DevTlsBundle,
    listen_addr: str = "127.0.0.1:50051",
) -> MASServerSettings:
    """Build ``MASServerSettings`` for a generated dev TLS bundle."""
    validate_dev_listen_addr(listen_addr)
    return MASServerSettings(
        listen_addr=listen_addr,
        tls=TlsConfig(
            server_cert_path=tls.server_cert,
            server_key_path=tls.server_key,
            client_ca_path=tls.ca_pem,
        ),
        agents=agents,
    )


async def grant_mesh(server: MASServer, agent_ids: list[str]) -> None:
    """Dev-only helper: allow each agent to message every other listed agent."""
    unique_ids = list(dict.fromkeys(agent_ids))
    for sender_id in unique_ids:
        allowed_targets = [target for target in unique_ids if target != sender_id]
        if not allowed_targets:
            continue
        await server.authz.set_permissions(
            sender_id,
            allowed_targets=allowed_targets,
        )


async def start_dev_server(
    *,
    agents: dict[str, AgentDefinition],
    cert_dir: Path,
    listen_addr: str = "127.0.0.1:50051",
    gateway: GatewaySettings | None = None,
    mesh: bool = False,
    agent_ids: list[str] | frozenset[str] | None = None,
) -> tuple[MASServer, DevTlsBundle]:
    """Start a local ``MASServer`` with generated dev TLS material in ``cert_dir``."""
    if not agents:
        raise ValueError("agents must include at least one allowlisted agent")
    ids = _resolve_agent_ids(agents, agent_ids)
    tls = generate_dev_tls(cert_dir, agent_ids=ids)
    settings = dev_server_settings(agents=agents, tls=tls, listen_addr=listen_addr)
    server = MASServer(settings=settings, gateway=gateway or GatewaySettings())
    await server.start()
    if mesh:
        await grant_mesh(server, list(agents))
    return server, tls

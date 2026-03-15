from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from mas_msp_contracts import AssetKind, CredentialRef, DiagnosticsCollect
from mas_msp_hosts import (
    DockerLinuxDiagnosticsBackend,
    DockerLinuxHostPoller,
    LinuxDiagnosticsAgent,
    LinuxPollingTarget,
)


CLIENT_ID = "11111111-1111-4111-8111-111111111111"
FABRIC_ID = "22222222-2222-4222-8222-222222222222"
INCIDENT_ID = "33333333-3333-4333-8333-333333333333"
ASSET_ID = "44444444-4444-4444-8444-444444444444"


@dataclass(slots=True)
class FakeDockerExecRunner:
    commands: list[str]

    async def run(self, *, container_name: str, command: str) -> str:
        self.commands.append(f"{container_name}:{command}")
        if command.startswith("if [ -r /etc/os-release ]"):
            return "Debian GNU/Linux 12"
        if command == "uname -r":
            return "6.1.0-test"
        if command == "cut -d' ' -f1 /proc/loadavg":
            return "0.42"
        if command.startswith("awk '/MemTotal/"):
            return "61.75"
        if command.startswith("for file in /proc/[0-9]*/comm"):
            return "busybox\nsh"
        if command.startswith("for file in /proc/[0-9]*/cmdline"):
            return "sh -c while true; do sleep 60; done\nsleep 60"
        if command.startswith("df -P /"):
            return "overlay 100 20 80 20% /"
        if command.startswith("if ls /var/log/*"):
            return "container booted\nservice ready"
        raise AssertionError(f"unexpected command: {command}")


@dataclass(slots=True)
class FakeDockerInspectRunner:
    inspections: list[str]

    async def inspect(self, *, container_name: str, format_string: str) -> str:
        self.inspections.append(f"{container_name}:{format_string}")
        if format_string == "{{.State.Status}}":
            return "running"
        if format_string == "{{if .State.Health}}{{.State.Health.Status}}{{end}}":
            return "healthy"
        raise AssertionError(f"unexpected inspect format: {format_string}")


def _asset():
    from mas_msp_contracts import AssetRef

    return AssetRef(
        asset_id=ASSET_ID,
        client_id=CLIENT_ID,
        fabric_id=FABRIC_ID,
        asset_kind=AssetKind.LINUX_HOST,
        vendor="Linux",
        model="Debian GNU/Linux 12",
        hostname="web-01",
        mgmt_address="172.18.0.10",
        site="docker-lab",
        tags=["linux", "docker"],
    )


def _target() -> LinuxPollingTarget:
    return LinuxPollingTarget(
        client_id=CLIENT_ID,
        fabric_id=FABRIC_ID,
        credential_ref=CredentialRef(
            credential_ref="cred-1",
            provider_kind="docker",
            purpose="linux-poll",
            secret_path="docker/local/mas-runtime",
        ),
        hostname="mas-runtime",
        mgmt_address="docker://mas-runtime",
        distribution="Debian GNU/Linux 12",
        site="docker",
        tags=["docker", "mas-system"],
    )


@pytest.mark.asyncio
async def test_docker_linux_backend_collects_summary_and_services() -> None:
    runner = FakeDockerExecRunner(commands=[])
    backend = DockerLinuxDiagnosticsBackend(
        container_name_resolver=lambda request: request.asset.hostname
        or request.asset.asset_id,
        runner=runner,
    )
    agent = LinuxDiagnosticsAgent(backend=backend)

    result = await agent.execute_diagnostics(
        DiagnosticsCollect(
            request_id="55555555-5555-4555-8555-555555555555",
            incident_id=INCIDENT_ID,
            client_id=CLIENT_ID,
            fabric_id=FABRIC_ID,
            asset=_asset(),
            diagnostic_profile="host.summary",
            requested_actions=["collect-service-status"],
            timeout_seconds=60,
            read_only=True,
        ),
        recent_activity=[{"payload": {"service_name": "sleep"}}],
    )

    assert result.evidence_bundle.summary.startswith(
        "Linux diagnostics captured host.summary"
    )
    assert result.evidence_bundle.items[1]["distribution"] == "Debian GNU/Linux 12"
    assert result.evidence_bundle.items[1]["kernel"] == "6.1.0-test"
    assert result.evidence_bundle.items[2]["services"][0] == {
        "service_name": "sleep",
        "service_state": "running",
    }
    assert any(command.startswith("web-01:uname -r") for command in runner.commands)


@pytest.mark.asyncio
async def test_docker_linux_host_poller_collects_running_container_observation() -> (
    None
):
    exec_runner = FakeDockerExecRunner(commands=[])
    inspect_runner = FakeDockerInspectRunner(inspections=[])
    poller = DockerLinuxHostPoller(
        container_name_resolver=lambda target: target.hostname or target.mgmt_address,
        exec_runner=exec_runner,
        inspect_runner=inspect_runner,
    )

    observation = await poller.poll(_target())

    assert observation.collected_at >= datetime(2020, 1, 1, tzinfo=UTC)
    assert observation.metrics["container_status"] == "running"
    assert observation.metrics["memory_percent"] == 61.75
    assert observation.metrics["disk_percent"] == 20.0
    assert observation.services == [
        {"service_name": "mas-runtime", "service_state": "running"}
    ]
    assert observation.findings == []
    assert inspect_runner.inspections == [
        "mas-runtime:{{.State.Status}}",
        "mas-runtime:{{if .State.Health}}{{.State.Health.Status}}{{end}}",
    ]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_linux_diagnostics_can_run_against_a_docker_container() -> None:
    if os.environ.get("MAS_MSP_RUN_DOCKER_TESTS") != "1":
        pytest.skip("set MAS_MSP_RUN_DOCKER_TESTS=1 to run Docker diagnostics tests")
    if not _docker_available():
        pytest.skip("docker daemon is not available")

    container_name = f"mas-msp-linux-diag-{uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            container_name,
            "busybox:1.36",
            "sh",
            "-c",
            "while true; do sleep 60; done",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        agent = LinuxDiagnosticsAgent(
            backend=DockerLinuxDiagnosticsBackend(
                container_name_resolver=lambda _request: container_name
            )
        )
        result = await agent.execute_diagnostics(
            DiagnosticsCollect(
                request_id="66666666-6666-4666-8666-666666666666",
                incident_id=INCIDENT_ID,
                client_id=CLIENT_ID,
                fabric_id=FABRIC_ID,
                asset=_asset(),
                diagnostic_profile="host.services",
                requested_actions=["collect-service-status"],
                timeout_seconds=60,
                read_only=True,
            ),
            recent_activity=[{"payload": {"service_name": "sleep"}}],
        )

        assert result.result.outcome == "completed"
        assert result.evidence_bundle.items[1]["services"][0] == {
            "service_name": "sleep",
            "service_state": "running",
        }
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            check=False,
            capture_output=True,
            text=True,
        )


def _docker_available() -> bool:
    result = subprocess.run(
        ["docker", "version", "--format", "{{.Server.Version}}"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and bool(result.stdout.strip())

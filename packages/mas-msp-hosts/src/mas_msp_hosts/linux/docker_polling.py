"""Docker-backed Linux host polling for local dogfood monitoring."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from mas_msp_contracts.types import JSONObject

from .docker import (
    CliDockerExecRunner,
    CliDockerInspectRunner,
    CliDockerStatsRunner,
    DockerExecRunner,
    DockerInspectRunner,
    DockerStatsRunner,
)
from .models import LinuxPollObservation, LinuxPollingTarget

type ContainerTargetNameResolver = Callable[[LinuxPollingTarget], str]


@dataclass(slots=True)
class DockerLinuxHostPoller:
    """Poll one Docker container as a constrained Linux host target."""

    container_name_resolver: ContainerTargetNameResolver | None = None
    exec_runner: DockerExecRunner | None = None
    inspect_runner: DockerInspectRunner | None = None
    stats_runner: DockerStatsRunner | None = None

    async def poll(self, target: LinuxPollingTarget) -> LinuxPollObservation:
        """Collect one normalized host observation from a Docker container."""

        container_name = self._container_name(target)
        service_name = target.hostname or container_name
        collected_at = datetime.now(UTC)
        status = await self._inspect_status(container_name)
        health_status = await self._inspect_health_status(container_name)
        if status != "running":
            return LinuxPollObservation(
                collected_at=collected_at,
                metrics={"container_status": status},
                services=[
                    {
                        "service_name": service_name,
                        "service_state": "stopped",
                    }
                ],
                findings=[
                    {
                        "code": "container_not_running",
                        "container_name": container_name,
                        "container_status": status,
                    }
                ],
            )

        memory_percent = await self._optional_float(
            container_name,
            (
                "awk '/MemTotal/ {total=$2} /MemAvailable/ {avail=$2} END "
                '{ if (total > 0) printf "%.2f", ((total-avail)/total)*100; }\' '
                "/proc/meminfo"
            ),
        )
        cpu_percent = await self._cpu_percent(container_name)
        disk_percent = await self._disk_percent(container_name)
        service_state = "unhealthy" if health_status == "unhealthy" else "running"
        findings: list[JSONObject] = []
        if health_status == "unhealthy":
            findings.append(
                {
                    "code": "container_unhealthy",
                    "container_name": container_name,
                    "health_status": health_status,
                }
            )
        metrics: JSONObject = {"container_status": status}
        if cpu_percent is not None:
            metrics["cpu_percent"] = cpu_percent
        if memory_percent is not None:
            metrics["memory_percent"] = memory_percent
        if disk_percent is not None:
            metrics["disk_percent"] = disk_percent
        return LinuxPollObservation(
            collected_at=collected_at,
            metrics=metrics,
            services=[
                {
                    "service_name": service_name,
                    "service_state": service_state,
                }
            ],
            findings=findings,
        )

    def _container_name(self, target: LinuxPollingTarget) -> str:
        if self.container_name_resolver is not None:
            return self.container_name_resolver(target)
        return target.hostname or target.mgmt_address

    async def _inspect_status(self, container_name: str) -> str:
        try:
            return (
                await self._inspect_runner().inspect(
                    container_name=container_name,
                    format_string="{{.State.Status}}",
                )
            ).strip()
        except RuntimeError:
            return "missing"

    async def _inspect_health_status(self, container_name: str) -> str | None:
        try:
            value = await self._inspect_runner().inspect(
                container_name=container_name,
                format_string="{{if .State.Health}}{{.State.Health.Status}}{{end}}",
            )
        except RuntimeError:
            return None
        normalized = value.strip()
        return normalized or None

    async def _cpu_percent(self, container_name: str) -> float | None:
        try:
            output = await self._stats_runner().stats(
                container_name=container_name,
                format_string="{{.CPUPerc}}",
            )
        except RuntimeError:
            return None
        normalized = output.strip().rstrip("%")
        if not normalized or not _is_float(normalized):
            return None
        return float(normalized)

    async def _disk_percent(self, container_name: str) -> float | None:
        output = await self._exec_runner().run(
            container_name=container_name,
            command="df -P / 2>/dev/null | tail -n 1 || df / 2>/dev/null | tail -n 1",
        )
        if not output:
            return None
        fields = output.split()
        if len(fields) < 6:
            return None
        used_percent = fields[4].rstrip("%")
        if not _is_float(used_percent):
            return None
        return float(used_percent)

    async def _optional_float(self, container_name: str, command: str) -> float | None:
        output = await self._exec_runner().run(
            container_name=container_name,
            command=command,
        )
        normalized = output.strip()
        if not normalized or not _is_float(normalized):
            return None
        return float(normalized)

    def _exec_runner(self) -> DockerExecRunner:
        return self.exec_runner or CliDockerExecRunner()

    def _inspect_runner(self) -> DockerInspectRunner:
        return self.inspect_runner or CliDockerInspectRunner()

    def _stats_runner(self) -> DockerStatsRunner:
        return self.stats_runner or CliDockerStatsRunner()


def _is_float(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


__all__ = ["DockerLinuxHostPoller"]

"""Docker-backed Linux diagnostics transport for local host testing."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from mas_msp_contracts import DiagnosticsCollect

from .backends import (
    LinuxDiagnosticsBackend,
    LinuxDiskSample,
    LinuxServiceSample,
    LinuxSummarySample,
)

type ContainerNameResolver = Callable[[DiagnosticsCollect], str]


class DockerExecRunner(Protocol):
    """Run one read-only command inside a Docker container."""

    async def run(self, *, container_name: str, command: str) -> str: ...


class DockerInspectRunner(Protocol):
    """Read one formatted field from `docker inspect`."""

    async def inspect(self, *, container_name: str, format_string: str) -> str: ...


class DockerStatsRunner(Protocol):
    """Read one formatted field from `docker stats --no-stream`."""

    async def stats(self, *, container_name: str, format_string: str) -> str: ...


@dataclass(frozen=True, slots=True)
class CliDockerExecRunner:
    """Execute read-only commands via `docker exec`."""

    async def run(self, *, container_name: str, command: str) -> str:
        process = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            container_name,
            "/bin/sh",
            "-lc",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(
                "docker exec failed for "
                f"{container_name}: {stderr.decode().strip() or command}"
            )
        return stdout.decode().strip()


@dataclass(frozen=True, slots=True)
class CliDockerInspectRunner:
    """Inspect one container field via `docker inspect`."""

    async def inspect(self, *, container_name: str, format_string: str) -> str:
        process = await asyncio.create_subprocess_exec(
            "docker",
            "inspect",
            "--format",
            format_string,
            container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(
                "docker inspect failed for "
                f"{container_name}: {stderr.decode().strip() or format_string}"
            )
        return stdout.decode().strip()


@dataclass(frozen=True, slots=True)
class CliDockerStatsRunner:
    """Inspect one container stats field via `docker stats --no-stream`."""

    async def stats(self, *, container_name: str, format_string: str) -> str:
        process = await asyncio.create_subprocess_exec(
            "docker",
            "stats",
            "--no-stream",
            "--format",
            format_string,
            container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(
                "docker stats failed for "
                f"{container_name}: {stderr.decode().strip() or format_string}"
            )
        return stdout.decode().strip()


class DockerLinuxDiagnosticsBackend(LinuxDiagnosticsBackend):
    """Collect Linux diagnostics by executing read-only commands in one container."""

    def __init__(
        self,
        *,
        container_name_resolver: ContainerNameResolver | None = None,
        runner: DockerExecRunner | None = None,
    ) -> None:
        self._container_name_resolver = (
            container_name_resolver or _default_container_name
        )
        self._runner = runner or CliDockerExecRunner()

    async def collect_summary(self, request: DiagnosticsCollect) -> LinuxSummarySample:
        """Collect a Linux host summary from one container."""

        container_name = self._container_name_resolver(request)
        distribution = _optional_text(
            await self._runner.run(
                container_name=container_name,
                command=(
                    "if [ -r /etc/os-release ]; then "
                    "sed -n 's/^PRETTY_NAME=\"\\(.*\\)\"$/\\1/p' /etc/os-release | "
                    "head -n 1; fi"
                ),
            )
        )
        kernel = _optional_text(
            await self._runner.run(
                container_name=container_name,
                command="uname -r",
            )
        )
        load_average = _optional_float(
            await self._runner.run(
                container_name=container_name,
                command="cut -d' ' -f1 /proc/loadavg",
            )
        )
        memory_percent = _optional_float(
            await self._runner.run(
                container_name=container_name,
                command=(
                    "awk '/MemTotal/ {total=$2} /MemAvailable/ {avail=$2} END "
                    '{ if (total > 0) printf "%.2f", ((total-avail)/total)*100; }\' '
                    "/proc/meminfo"
                ),
            )
        )
        return LinuxSummarySample(
            hostname=request.asset.hostname or request.asset.asset_id,
            platform="linux",
            distribution=distribution,
            kernel=kernel,
            load_average=load_average,
            memory_percent=memory_percent,
        )

    async def collect_services(
        self,
        request: DiagnosticsCollect,
        *,
        hinted_service: str | None,
    ) -> list[LinuxServiceSample]:
        """Collect Linux process-backed service state from one container."""

        container_name = self._container_name_resolver(request)
        process_output = await self._runner.run(
            container_name=container_name,
            command=(
                "for file in /proc/[0-9]*/comm; do "
                'cat "$file"; '
                "done 2>/dev/null | sort -u"
            ),
        )
        process_names = [
            line.strip() for line in process_output.splitlines() if line.strip()
        ]
        cmdline_output = await self._runner.run(
            container_name=container_name,
            command=(
                "for file in /proc/[0-9]*/cmdline; do "
                "tr '\\0' ' ' < \"$file\"; "
                "echo; "
                "done 2>/dev/null | sed '/^$/d'"
            ),
        )
        cmdlines = [
            line.strip() for line in cmdline_output.splitlines() if line.strip()
        ]
        services: list[LinuxServiceSample] = []
        if hinted_service:
            services.append(
                LinuxServiceSample(
                    service_name=hinted_service,
                    service_state=(
                        "running"
                        if _hinted_service_is_running(
                            hinted_service,
                            process_names=process_names,
                            cmdlines=cmdlines,
                        )
                        else "stopped"
                    ),
                )
            )
        for process_name in process_names:
            if hinted_service and process_name == hinted_service:
                continue
            services.append(
                LinuxServiceSample(
                    service_name=process_name,
                    service_state="running",
                )
            )
            if len(services) >= 5:
                break
        return services

    async def collect_disk(self, request: DiagnosticsCollect) -> list[LinuxDiskSample]:
        """Collect Linux disk usage from one container."""

        container_name = self._container_name_resolver(request)
        output = await self._runner.run(
            container_name=container_name,
            command="df -P / 2>/dev/null | tail -n 1 || df / 2>/dev/null | tail -n 1",
        )
        if not output:
            return []
        fields = output.split()
        if len(fields) < 6:
            return []
        used_percent = fields[4].rstrip("%")
        mount = fields[5]
        if not _is_float(used_percent):
            return []
        return [LinuxDiskSample(mount=mount, used_percent=float(used_percent))]

    async def collect_logs(
        self,
        request: DiagnosticsCollect,
        *,
        hinted_service: str | None,
    ) -> list[str]:
        """Collect Linux log excerpts from one container."""

        del hinted_service
        container_name = self._container_name_resolver(request)
        output = await self._runner.run(
            container_name=container_name,
            command=(
                "if ls /var/log/* >/dev/null 2>&1; then "
                "for file in /var/log/*; do "
                'if [ -f "$file" ]; then tail -n 5 "$file" && exit 0; fi; '
                "done; "
                "fi; "
                "head -n 5 /proc/1/status"
            ),
        )
        return [line for line in output.splitlines() if line.strip()]


def _default_container_name(request: DiagnosticsCollect) -> str:
    return request.asset.hostname or request.asset.asset_id


def _optional_text(value: str) -> str | None:
    normalized = value.strip()
    return normalized or None


def _optional_float(value: str) -> float | None:
    normalized = value.strip()
    if not normalized or not _is_float(normalized):
        return None
    return float(normalized)


def _is_float(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _hinted_service_is_running(
    hinted_service: str,
    *,
    process_names: list[str],
    cmdlines: list[str],
) -> bool:
    if hinted_service in process_names:
        return True
    for cmdline in cmdlines:
        for token in cmdline.split():
            normalized = token.rsplit("/", 1)[-1]
            if normalized == hinted_service:
                return True
    return False


__all__ = [
    "CliDockerExecRunner",
    "CliDockerInspectRunner",
    "CliDockerStatsRunner",
    "DockerExecRunner",
    "DockerInspectRunner",
    "DockerStatsRunner",
    "DockerLinuxDiagnosticsBackend",
]

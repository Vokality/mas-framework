"""Docker-backed MAS runtime monitoring for local dogfood deployments."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from mas_msp_contracts import AlertRaised, CredentialRef, HealthSnapshot
from mas_msp_hosts import (
    DockerLinuxHostPoller,
    LinuxEventIngestAgent,
    LinuxHostPoller,
    LinuxJournalEvent,
    LinuxPollingAgent,
    LinuxPollingTarget,
)

from mas_ops_api.settings import OpsApiSettings
from mas_ops_api.visibility import InProcessVisibilityRuntime

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DogfoodProblemWindow:
    """Track one active degraded condition until it clears or alerts."""

    signature: str
    first_seen_at: datetime
    alerted: bool = False


class DockerDogfoodMonitorService:
    """Monitor the local MAS runtime container as a Linux host asset."""

    def __init__(
        self,
        *,
        settings: OpsApiSettings,
        visibility_runtime: InProcessVisibilityRuntime,
        poller: LinuxHostPoller | None = None,
    ) -> None:
        self._settings = settings
        self._visibility_runtime = visibility_runtime
        self._poller = poller or DockerLinuxHostPoller(
            container_name_resolver=lambda _target: self._settings.dogfood_container_name
        )
        self._polling_agent = LinuxPollingAgent(poller=self._poller)
        self._event_ingest_agent = LinuxEventIngestAgent()
        self._task: asyncio.Task[None] | None = None
        self._started_at: datetime | None = None
        self._problem_window: DogfoodProblemWindow | None = None

    def start(self) -> None:
        """Start the hosted monitoring loop if enabled."""

        if not self._settings.dogfood_enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop the hosted monitoring loop."""

        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def _run_loop(self) -> None:
        try:
            while True:
                try:
                    await self._poll_once()
                except Exception:
                    logger.exception("dogfood monitor poll failed")
                await asyncio.sleep(self._settings.dogfood_poll_seconds)
        except asyncio.CancelledError:
            raise

    async def _poll_once(self) -> None:
        target = self._build_target()
        observation = await self._poller.poll(target)
        raw_snapshot = self._polling_agent.normalize_poll(target, observation)
        snapshot = await self._ingest_snapshot(raw_snapshot)
        self._record_start_time(snapshot)

        problem_signature = _problem_signature(snapshot)
        if problem_signature is None:
            self._problem_window = None
            return
        if self._problem_window is None or (
            self._problem_window.signature != problem_signature
        ):
            self._problem_window = DogfoodProblemWindow(
                signature=problem_signature,
                first_seen_at=snapshot.collected_at.astimezone(UTC),
            )
            return
        if self._problem_window.alerted or not self._problem_grace_satisfied(snapshot):
            return
        alert = self._event_ingest_agent.normalize_journal_event(
            LinuxJournalEvent(
                client_id=target.client_id,
                fabric_id=target.fabric_id,
                occurred_at=snapshot.collected_at,
                message=_alert_message(snapshot),
                level="error",
                hostname=target.hostname,
                mgmt_address=target.mgmt_address,
                distribution=target.distribution,
                site=target.site,
                service_name=target.hostname,
                source_kind="docker_poll",
                tags=target.tags,
            )
        )
        await self._ingest_alert(alert)
        self._problem_window.alerted = True

    async def _ingest_snapshot(self, snapshot: HealthSnapshot) -> HealthSnapshot:
        result = await self._visibility_runtime.ingest_contract(snapshot)
        if not isinstance(result, HealthSnapshot):
            raise TypeError("snapshot ingestion must return a HealthSnapshot")
        return result

    async def _ingest_alert(self, alert: AlertRaised) -> AlertRaised:
        result = await self._visibility_runtime.ingest_contract(alert)
        if not isinstance(result, AlertRaised):
            raise TypeError("alert ingestion must return an AlertRaised")
        return result

    def _build_target(self) -> LinuxPollingTarget:
        return LinuxPollingTarget(
            client_id=self._settings.dogfood_client_id,
            fabric_id=self._settings.dogfood_fabric_id,
            credential_ref=CredentialRef(
                credential_ref="docker-local-runtime",
                provider_kind="docker",
                purpose="linux-poll",
                secret_path=f"docker/{self._settings.dogfood_container_name}",
            ),
            hostname=self._settings.dogfood_hostname,
            mgmt_address=self._settings.dogfood_mgmt_address,
            distribution=self._settings.dogfood_distribution,
            site=self._settings.dogfood_site,
            tags=list(self._settings.dogfood_tags),
        )

    def _record_start_time(self, snapshot: HealthSnapshot) -> None:
        if self._started_at is None:
            self._started_at = snapshot.collected_at.astimezone(UTC)

    def _problem_grace_satisfied(self, snapshot: HealthSnapshot) -> bool:
        if self._started_at is None or self._problem_window is None:
            return False
        startup_age = (
            snapshot.collected_at.astimezone(UTC) - self._started_at
        ).total_seconds()
        problem_age = (
            snapshot.collected_at.astimezone(UTC) - self._problem_window.first_seen_at
        ).total_seconds()
        return (
            startup_age >= self._settings.dogfood_startup_grace_seconds
            and problem_age >= self._settings.dogfood_problem_grace_seconds
        )


def _problem_signature(snapshot: HealthSnapshot) -> str | None:
    if snapshot.health_state.value == "healthy":
        return None
    services = snapshot.metrics.get("services")
    if isinstance(services, list) and services:
        first = services[0]
        if isinstance(first, dict):
            service_name = first.get("service_name")
            service_state = first.get("service_state")
            if isinstance(service_name, str) and isinstance(service_state, str):
                return f"{service_name}:{service_state}"
    for finding in snapshot.findings:
        if not isinstance(finding, dict):
            continue
        code = finding.get("code")
        if isinstance(code, str) and code:
            return code
    return snapshot.health_state.value


def _alert_message(snapshot: HealthSnapshot) -> str:
    services = snapshot.metrics.get("services")
    if isinstance(services, list) and services:
        first = services[0]
        if isinstance(first, dict):
            service_name = first.get("service_name")
            service_state = first.get("service_state")
            if isinstance(service_name, str) and isinstance(service_state, str):
                if service_state == "stopped":
                    return f"{service_name} container is not running"
                if service_state == "unhealthy":
                    return f"{service_name} container reported unhealthy status"
    return "MAS runtime host entered a degraded state"


__all__ = ["DockerDogfoodMonitorService"]

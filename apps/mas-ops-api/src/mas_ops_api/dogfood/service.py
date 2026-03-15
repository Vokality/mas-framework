"""Docker-backed MAS runtime monitoring for local dogfood deployments."""

from __future__ import annotations

import asyncio
import logging

from mas_msp_contracts import AlertRaised, CredentialRef, HealthSnapshot
from mas_msp_core import InventoryRepository, OpsBridgeAgent
from mas_msp_hosts import (
    DockerLinuxHostPoller,
    LinuxEventIngestAgent,
    LinuxHostPoller,
    LinuxJournalEvent,
    LinuxPollingAgent,
    LinuxPollingTarget,
)

from mas_ops_api.connectors import PortfolioIngressRegistry
from mas_ops_api.settings import OpsApiSettings

logger = logging.getLogger(__name__)


class DockerDogfoodMonitorService:
    """Monitor the local MAS runtime container as a Linux host asset."""

    def __init__(
        self,
        *,
        settings: OpsApiSettings,
        portfolio_ingress_registry: PortfolioIngressRegistry,
        poller: LinuxHostPoller | None = None,
    ) -> None:
        self._settings = settings
        self._portfolio_ingress_registry = portfolio_ingress_registry
        self._inventory = InventoryRepository()
        self._bridge = OpsBridgeAgent()
        self._poller = poller or DockerLinuxHostPoller(
            container_name_resolver=lambda _target: self._settings.dogfood_container_name
        )
        self._polling_agent = LinuxPollingAgent(poller=self._poller)
        self._event_ingest_agent = LinuxEventIngestAgent()
        self._task: asyncio.Task[None] | None = None
        self._last_problem_signature: str | None = None

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
        snapshot = self._polling_agent.normalize_poll(target, observation)
        await self._ingest_contract(snapshot)

        problem_signature = _problem_signature(snapshot)
        if problem_signature is None:
            self._last_problem_signature = None
            return
        if problem_signature == self._last_problem_signature:
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
        await self._ingest_contract(alert)
        self._last_problem_signature = problem_signature

    async def _ingest_contract(
        self,
        contract: HealthSnapshot | AlertRaised,
    ) -> None:
        if isinstance(contract, HealthSnapshot):
            publish = self._inventory.process_snapshot(contract)
        else:
            publish = self._inventory.process_alert(contract)
        connector = self._portfolio_ingress_registry.get(publish.asset.client_id)
        for event in self._bridge.build_portfolio_events(publish):
            await connector.ingest_portfolio_event(event=event)

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

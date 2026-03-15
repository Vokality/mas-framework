"""In-process fabric-local visibility runtime for the ops app."""

from __future__ import annotations

from mas_msp_contracts import AlertRaised, AssetKind, HealthSnapshot
from mas_msp_core import AlertPolicyAgent, InventoryRepository, OpsBridgeAgent, PortfolioPublish

from mas_ops_api.connectors import PortfolioIngressRegistry


class InProcessVisibilityRuntime:
    """Run inventory binding, alerting, and portfolio publication in process."""

    def __init__(
        self,
        *,
        alert_policy_agent: AlertPolicyAgent,
        bridge: OpsBridgeAgent,
        portfolio_ingress_registry: PortfolioIngressRegistry,
        repository: InventoryRepository | None = None,
    ) -> None:
        self._alert_policy_agent = alert_policy_agent
        self._bridge = bridge
        self._portfolio_ingress_registry = portfolio_ingress_registry
        self._repository = repository or InventoryRepository()

    async def ingest_contract(
        self,
        contract: HealthSnapshot | AlertRaised,
    ) -> HealthSnapshot | AlertRaised:
        """Ingest one raw visibility contract through the local fabric path."""

        if isinstance(contract, HealthSnapshot):
            return await self._ingest_snapshot(contract)
        return await self._ingest_alert(contract)

    async def _ingest_alert(self, alert: AlertRaised) -> AlertRaised:
        bound_alert = self._repository.bind_alert(alert)
        surfaced_alerts = await self._alert_policy_agent.process_source_alert(
            bound_alert.alert
        )
        for index, surfaced_alert in enumerate(surfaced_alerts):
            await self._publish(
                PortfolioPublish(
                    asset=surfaced_alert.asset,
                    asset_upserted=bound_alert.asset_upserted and index == 0,
                    health_changed=False,
                    source=surfaced_alert,
                )
            )
        return bound_alert.alert

    async def _ingest_snapshot(self, snapshot: HealthSnapshot) -> HealthSnapshot:
        if snapshot.asset.asset_kind not in {AssetKind.LINUX_HOST, AssetKind.WINDOWS_HOST}:
            await self._publish(self._repository.process_snapshot(snapshot))
            return snapshot
        bound_snapshot = self._repository.bind_snapshot(snapshot)
        outcome = await self._alert_policy_agent.process_host_snapshot(
            bound_snapshot.snapshot
        )
        health_changed = self._repository.record_snapshot_health(
            outcome.authoritative_snapshot
        )
        await self._publish(
            PortfolioPublish(
                asset=outcome.authoritative_snapshot.asset,
                asset_upserted=bound_snapshot.asset_upserted,
                health_changed=health_changed,
                source=outcome.authoritative_snapshot,
            )
        )
        for surfaced_alert in outcome.surfaced_alerts:
            await self._publish(
                PortfolioPublish(
                    asset=surfaced_alert.asset,
                    asset_upserted=False,
                    health_changed=False,
                    source=surfaced_alert,
                )
            )
        return outcome.authoritative_snapshot

    async def _publish(self, publish_request: PortfolioPublish) -> None:
        connector = self._portfolio_ingress_registry.get(publish_request.asset.client_id)
        for event in self._bridge.build_portfolio_events(publish_request):
            await connector.ingest_portfolio_event(event=event)

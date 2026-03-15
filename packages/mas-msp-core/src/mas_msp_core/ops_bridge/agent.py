"""Ops bridge agent for phase 2 visibility, phase 3 chat, and phase 4 control."""

from __future__ import annotations

from datetime import UTC
from uuid import uuid4

from mas_agent import Agent, AgentMessage, TlsClientConfig
from mas_msp_contracts import (
    ApprovalDecision,
    AlertRaised,
    AssetKind,
    ConfigValidationResult,
    HealthSnapshot,
    IncidentRecord,
    OperatorChatRequest,
    OperatorChatResponse,
    PortfolioEvent,
    RemediationExecute,
)

from mas_msp_core.agent_ids import OPS_BRIDGE_AGENT_ID, build_ops_plane_connector_id
from mas_msp_core.approvals import (
    ApprovalCancellation,
    ApprovalController,
    ApprovalRecord,
)
from mas_msp_core.config import ConfigDeployerAgent
from mas_msp_core.incidents import (
    IncidentChatHandler,
    IncidentRemediationExecution,
    IncidentRemediationHandler,
    VisibilityAlertHandler,
)
from mas_msp_core.messages import PortfolioPublish


class OpsBridgeAgent(Agent[dict[str, object]]):
    """Convert resolved visibility payloads into ops-plane portfolio events."""

    def __init__(
        self,
        *,
        server_addr: str = "localhost:50051",
        tls: TlsClientConfig | None = None,
        incident_chat_handler: IncidentChatHandler | None = None,
        visibility_alert_handler: VisibilityAlertHandler | None = None,
        incident_remediation_handler: IncidentRemediationHandler | None = None,
        approval_controller: ApprovalController | None = None,
        config_deployer: ConfigDeployerAgent | None = None,
    ) -> None:
        super().__init__(
            OPS_BRIDGE_AGENT_ID,
            capabilities=["msp:portfolio:bridge"],
            server_addr=server_addr,
            tls=tls,
        )
        self._incident_chat_handler = incident_chat_handler
        self._visibility_alert_handler = visibility_alert_handler
        self._incident_remediation_handler = incident_remediation_handler
        self._approval_controller = approval_controller
        self._config_deployer = config_deployer

    @Agent.on("portfolio.publish", model=PortfolioPublish)
    async def handle_portfolio_publish(
        self,
        _message: AgentMessage,
        publish_request: PortfolioPublish,
    ) -> None:
        connector_id = build_ops_plane_connector_id(publish_request.asset.client_id)
        for event in self.build_portfolio_events(publish_request):
            await self.send(
                connector_id,
                "portfolio.event",
                event.model_dump(mode="json"),
            )

    def build_portfolio_events(
        self,
        publish_request: PortfolioPublish,
    ) -> list[PortfolioEvent]:
        """Return the additive portfolio events created from one visibility update."""

        source = publish_request.source
        if source.client_id != publish_request.asset.client_id:
            raise ValueError(
                "publish request client_id must match resolved asset client_id"
            )
        if source.fabric_id != publish_request.asset.fabric_id:
            raise ValueError(
                "publish request fabric_id must match resolved asset fabric_id"
            )

        events: list[PortfolioEvent] = []
        if publish_request.asset_upserted:
            events.append(
                self._build_event(
                    client_id=publish_request.asset.client_id,
                    fabric_id=publish_request.asset.fabric_id,
                    event_type="asset.upserted",
                    subject_type="asset",
                    subject_id=publish_request.asset.asset_id,
                    occurred_at=self._event_time(source),
                    payload={
                        "asset": publish_request.asset.model_dump(mode="json"),
                        "source_reference": self._source_reference(source),
                    },
                )
            )

        if isinstance(source, AlertRaised):
            events.append(
                self._build_event(
                    client_id=source.client_id,
                    fabric_id=source.fabric_id,
                    event_type=_alert_event_type(source.asset.asset_kind),
                    subject_type="alert",
                    subject_id=source.alert_id,
                    occurred_at=source.occurred_at,
                    payload={"alert": source.model_dump(mode="json")},
                )
            )
            return events

        if publish_request.health_changed:
            events.append(
                self._build_event(
                    client_id=source.client_id,
                    fabric_id=source.fabric_id,
                    event_type="asset.health.changed",
                    subject_type="asset",
                    subject_id=publish_request.asset.asset_id,
                    occurred_at=source.collected_at,
                    payload={
                        "asset": publish_request.asset.model_dump(mode="json"),
                        "snapshot": source.model_dump(mode="json"),
                    },
                )
            )
        events.append(
            self._build_event(
                client_id=source.client_id,
                fabric_id=source.fabric_id,
                event_type=_snapshot_event_type(publish_request.asset.asset_kind),
                subject_type="snapshot",
                subject_id=source.snapshot_id,
                occurred_at=source.collected_at,
                payload={"snapshot": source.model_dump(mode="json")},
            )
        )
        return events

    @staticmethod
    def _build_event(
        *,
        client_id: str,
        fabric_id: str,
        event_type: str,
        subject_type: str,
        subject_id: str,
        occurred_at,
        payload: dict[str, object],
    ) -> PortfolioEvent:
        return PortfolioEvent(
            event_id=str(uuid4()),
            client_id=client_id,
            fabric_id=fabric_id,
            event_type=event_type,
            subject_type=subject_type,
            subject_id=subject_id,
            occurred_at=occurred_at.astimezone(UTC),
            payload_version=1,
            payload=payload,
        )

    @staticmethod
    def _event_time(source: AlertRaised | HealthSnapshot):
        if isinstance(source, AlertRaised):
            return source.occurred_at
        return source.collected_at

    @staticmethod
    def _source_reference(source: AlertRaised | HealthSnapshot) -> dict[str, str]:
        if isinstance(source, AlertRaised):
            return {"source_type": "alert", "source_id": source.alert_id}
        return {"source_type": "snapshot", "source_id": source.snapshot_id}

    async def dispatch_chat_request(
        self,
        *,
        request: OperatorChatRequest,
    ) -> OperatorChatResponse:
        """Route one incident-scoped chat request into fabric-local orchestration."""

        if self._incident_chat_handler is None:
            raise LookupError("no incident chat handler is configured")
        return await self._incident_chat_handler.handle_incident_chat_request(request)

    async def dispatch_visibility_alert(
        self,
        *,
        event: PortfolioEvent,
    ) -> IncidentRecord:
        """Route one visibility alert into fabric-local incident ownership."""

        if event.event_type not in {"network.alert.raised", "host.alert.raised"}:
            raise ValueError("visibility dispatch requires a supported alert event")
        if self._visibility_alert_handler is None:
            raise LookupError("no visibility alert handler is configured")
        alert = AlertRaised.model_validate(event.payload["alert"])
        return await self._visibility_alert_handler.handle_visibility_alert(alert)

    async def dispatch_approval_decision(
        self,
        *,
        decision: ApprovalDecision,
    ) -> ApprovalRecord:
        """Route one approval decision into the phase-4 approval controller."""

        if self._approval_controller is None:
            raise LookupError("no approval controller is configured")
        return await self._approval_controller.apply_decision(decision)

    async def dispatch_approved_remediation(
        self,
        *,
        approval_id: str,
        remediation: RemediationExecute,
    ) -> IncidentRemediationExecution:
        """Route one approved remediation into fabric-local execution."""

        if self._incident_remediation_handler is None:
            raise LookupError("no incident remediation handler is configured")
        return await self._incident_remediation_handler.execute_approved_remediation(
            approval_id=approval_id,
            remediation=remediation,
        )

    async def dispatch_approval_cancellation(
        self,
        *,
        cancellation: ApprovalCancellation,
    ) -> ApprovalRecord:
        """Route one approval cancellation into the phase-4 approval controller."""

        if self._approval_controller is None:
            raise LookupError("no approval controller is configured")
        return await self._approval_controller.cancel_request(cancellation)

    async def dispatch_config_validation(
        self,
        *,
        config_apply_run_id: str,
    ) -> ConfigValidationResult:
        """Route one validation request into the config deployer."""

        if self._config_deployer is None:
            raise LookupError("no config deployer is configured")
        return await self._config_deployer.validate_run(config_apply_run_id)

    async def dispatch_config_apply(
        self,
        *,
        config_apply_run_id: str,
    ) -> ApprovalRecord:
        """Route one config-apply request into the config deployer."""

        if self._config_deployer is None:
            raise LookupError("no config deployer is configured")
        return await self._config_deployer.request_apply(config_apply_run_id)


__all__ = ["OpsBridgeAgent"]


def _alert_event_type(asset_kind: AssetKind) -> str:
    if asset_kind in {AssetKind.LINUX_HOST, AssetKind.WINDOWS_HOST}:
        return "host.alert.raised"
    return "network.alert.raised"


def _snapshot_event_type(asset_kind: AssetKind) -> str:
    if asset_kind in {AssetKind.LINUX_HOST, AssetKind.WINDOWS_HOST}:
        return "host.snapshot.recorded"
    return "network.snapshot.recorded"

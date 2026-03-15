"""Per-client command connector registry for the ops plane."""

from __future__ import annotations

from collections.abc import Callable

from mas_msp_contracts import (
    ApprovalDecision,
    ChatTurnState,
    ConfigValidationResult,
    OperatorChatRequest,
    OperatorChatResponse,
    PortfolioEvent,
)
from mas_msp_core import ApprovalCancellation

from mas_ops_api.connectors.protocol import FabricConnector


class NullFabricConnector:
    """Default command connector used before fabric write paths are implemented."""

    async def dispatch_chat_request(
        self,
        *,
        request: OperatorChatRequest,
    ) -> OperatorChatResponse:
        return OperatorChatResponse(
            request_id=request.request_id,
            chat_session_id=request.chat_session_id,
            turn_id=request.turn_id,
            state=ChatTurnState.FAILED,
            incident_id=request.incident_id,
            markdown_summary="No incident connector is configured for this client.",
            evidence_bundle_ids=[],
            approval_id=None,
            recommended_actions=[],
        )

    async def dispatch_visibility_event(
        self,
        *,
        event: PortfolioEvent,
    ) -> None:
        del event
        return None

    async def dispatch_approval_decision(
        self,
        *,
        decision: ApprovalDecision,
    ) -> None:
        del decision
        return None

    async def dispatch_approval_cancellation(
        self,
        *,
        cancellation: ApprovalCancellation,
    ) -> None:
        del cancellation
        return None

    async def request_config_validation(
        self,
        *,
        client_id: str,
        config_apply_run_id: str,
    ) -> ConfigValidationResult:
        del client_id, config_apply_run_id
        raise LookupError("no config validation connector is configured")

    async def request_config_apply(
        self,
        *,
        client_id: str,
        config_apply_run_id: str,
    ) -> None:
        return None


class ConnectorRegistry:
    """Resolve one least-privilege connector per client fabric."""

    def __init__(
        self,
        *,
        factory: Callable[[str], FabricConnector] | None = None,
    ) -> None:
        self._default = NullFabricConnector()
        self._factory = factory
        self._connectors: dict[str, FabricConnector] = {}

    def register(self, client_id: str, connector: FabricConnector) -> None:
        """Register a connector for one client."""

        self._connectors[client_id] = connector

    def get(self, client_id: str) -> FabricConnector:
        """Return the connector for the target client, or a null connector."""

        connector = self._connectors.get(client_id)
        if connector is not None:
            return connector
        if self._factory is None:
            return self._default
        connector = self._factory(client_id)
        self._connectors[client_id] = connector
        return connector


__all__ = ["ConnectorRegistry", "NullFabricConnector"]

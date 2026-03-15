"""Phase 3 architecture seam tests for connector and bridge ownership."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from mas_msp_contracts import ChatTurnState, OperatorChatRequest, OperatorChatResponse

from .conftest import CLIENT_A
from .test_portfolio_ingest import _alert_event, _seed_desired_state


@dataclass(slots=True)
class FakeFabricConnector:
    visibility_events: list[object] = field(default_factory=list)

    async def dispatch_chat_request(
        self,
        *,
        request: OperatorChatRequest,
    ) -> OperatorChatResponse:
        return OperatorChatResponse(
            request_id=request.request_id,
            chat_session_id=request.chat_session_id,
            turn_id=request.turn_id,
            state=ChatTurnState.COMPLETED,
            incident_id=request.incident_id,
            markdown_summary="unused",
            evidence_bundle_ids=[],
            approval_id=None,
            recommended_actions=[],
        )

    async def dispatch_visibility_event(self, *, event) -> None:  # noqa: ANN001
        self.visibility_events.append(event)

    async def request_config_validation(
        self,
        *,
        client_id: str,
        config_apply_run_id: str,
    ) -> None:
        del client_id, config_apply_run_id
        return None

    async def request_config_apply(
        self,
        *,
        client_id: str,
        config_apply_run_id: str,
    ) -> None:
        del client_id, config_apply_run_id
        return None


@pytest.mark.asyncio
async def test_portfolio_ingest_dispatches_alerts_to_client_connector(
    ops_app,
    session_factory,
) -> None:
    await _seed_desired_state(session_factory)
    connector = FakeFabricConnector()
    ops_app.state.services.command_connector_registry.register(CLIENT_A, connector)

    ingress = ops_app.state.services.portfolio_ingress_registry.get(CLIENT_A)
    await ingress.ingest_portfolio_event(event=_alert_event())

    assert connector.visibility_events == [_alert_event()]

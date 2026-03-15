"""In-process connector used for local fabric orchestration."""

from __future__ import annotations

from mas_msp_contracts import (
    ApprovalDecision,
    ConfigValidationResult,
    OperatorChatRequest,
    OperatorChatResponse,
    PortfolioEvent,
    RemediationExecute,
)

from mas_msp_core import ApprovalCancellation, OpsBridgeAgent


class InProcessFabricConnector:
    """Dispatch incident chat requests into the local bridge/runtime."""

    def __init__(self, *, client_id: str, bridge: OpsBridgeAgent) -> None:
        self._client_id = client_id
        self._bridge = bridge

    async def dispatch_chat_request(
        self,
        *,
        request: OperatorChatRequest,
    ) -> OperatorChatResponse:
        """Forward one chat request into the local fabric bridge."""

        if request.client_id != self._client_id:
            raise ValueError("chat request client_id must match connector client_id")
        return await self._bridge.dispatch_chat_request(request=request)

    async def dispatch_visibility_event(
        self,
        *,
        event: PortfolioEvent,
    ) -> None:
        """Forward one visibility alert into fabric-local incident ownership."""

        if event.client_id != self._client_id:
            raise ValueError(
                "visibility event client_id must match connector client_id"
            )
        await self._bridge.dispatch_visibility_alert(event=event)

    async def dispatch_approval_decision(
        self,
        *,
        decision: ApprovalDecision,
    ) -> None:
        """Forward one approval decision into the local bridge."""

        await self._bridge.dispatch_approval_decision(decision=decision)

    async def dispatch_approved_remediation(
        self,
        *,
        approval_id: str,
        remediation: RemediationExecute,
    ):
        """Forward one approved remediation into the local bridge/runtime."""

        if remediation.client_id != self._client_id:
            raise ValueError(
                "approved remediation client_id must match connector client_id"
            )
        return await self._bridge.dispatch_approved_remediation(
            approval_id=approval_id,
            remediation=remediation,
        )

    async def dispatch_approval_cancellation(
        self,
        *,
        cancellation: ApprovalCancellation,
    ) -> None:
        """Forward one approval cancellation into the local bridge."""

        await self._bridge.dispatch_approval_cancellation(cancellation=cancellation)

    async def request_config_validation(
        self,
        *,
        client_id: str,
        config_apply_run_id: str,
    ) -> ConfigValidationResult:
        if client_id != self._client_id:
            raise ValueError(
                "config validation client_id must match connector client_id"
            )
        return await self._bridge.dispatch_config_validation(
            config_apply_run_id=config_apply_run_id
        )

    async def request_config_apply(
        self,
        *,
        client_id: str,
        config_apply_run_id: str,
    ) -> None:
        if client_id != self._client_id:
            raise ValueError("config apply client_id must match connector client_id")
        await self._bridge.dispatch_config_apply(
            config_apply_run_id=config_apply_run_id
        )


__all__ = ["InProcessFabricConnector"]

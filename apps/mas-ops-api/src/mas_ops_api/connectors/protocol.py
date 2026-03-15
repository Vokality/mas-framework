"""Command connector protocol for ops-plane writes into client fabrics."""

from __future__ import annotations

from typing import Protocol

from mas_msp_contracts import (
    ApprovalDecision,
    ConfigValidationResult,
    OperatorChatRequest,
    OperatorChatResponse,
    PortfolioEvent,
    RemediationExecute,
)
from mas_msp_core import (
    ApprovalCancellation,
    IncidentRemediationExecution,
)


class FabricConnector(Protocol):
    """Least-privilege per-client command connector interface."""

    async def dispatch_chat_request(
        self,
        *,
        request: OperatorChatRequest,
    ) -> OperatorChatResponse: ...

    async def dispatch_visibility_event(
        self,
        *,
        event: PortfolioEvent,
    ) -> None: ...

    async def dispatch_approval_decision(
        self,
        *,
        decision: ApprovalDecision,
    ) -> None: ...

    async def dispatch_approved_remediation(
        self,
        *,
        approval_id: str,
        remediation: RemediationExecute,
    ) -> IncidentRemediationExecution: ...

    async def dispatch_approval_cancellation(
        self,
        *,
        cancellation: ApprovalCancellation,
    ) -> None: ...

    async def request_config_validation(
        self,
        *,
        client_id: str,
        config_apply_run_id: str,
    ) -> ConfigValidationResult: ...

    async def request_config_apply(
        self,
        *,
        client_id: str,
        config_apply_run_id: str,
    ) -> None: ...


__all__ = ["FabricConnector"]

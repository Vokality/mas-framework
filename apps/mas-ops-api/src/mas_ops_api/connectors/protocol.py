"""Command connector protocol for ops-plane writes into client fabrics."""

from __future__ import annotations

from typing import Protocol

from mas_msp_contracts import OperatorChatRequest, OperatorChatResponse, PortfolioEvent


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

    async def request_config_validation(
        self,
        *,
        client_id: str,
        config_apply_run_id: str,
    ) -> None: ...

    async def request_config_apply(
        self,
        *,
        client_id: str,
        config_apply_run_id: str,
    ) -> None: ...


__all__ = ["FabricConnector"]

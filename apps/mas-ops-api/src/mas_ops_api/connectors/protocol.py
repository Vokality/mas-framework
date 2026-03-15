"""Command connector protocol for ops-plane writes into client fabrics."""

from __future__ import annotations

from typing import Protocol


class FabricConnector(Protocol):
    """Least-privilege per-client command connector interface."""

    async def dispatch_chat_turn(
        self,
        *,
        client_id: str,
        chat_session_id: str,
        turn_id: str,
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

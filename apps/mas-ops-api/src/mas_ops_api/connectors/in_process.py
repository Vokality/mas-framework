"""In-process connector used for local Phase 3 fabric orchestration."""

from __future__ import annotations

from mas_msp_contracts import OperatorChatRequest, OperatorChatResponse, PortfolioEvent

from mas_msp_core import OpsBridgeAgent


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
            raise ValueError("visibility event client_id must match connector client_id")
        await self._bridge.dispatch_visibility_alert(event=event)

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


__all__ = ["InProcessFabricConnector"]

"""Per-client command connector registry for the ops plane."""

from __future__ import annotations

from collections.abc import Callable

from mas_ops_api.connectors.protocol import FabricConnector


class NullFabricConnector:
    """Default command connector used before fabric write paths are implemented."""

    async def dispatch_chat_turn(
        self,
        *,
        client_id: str,
        chat_session_id: str,
        turn_id: str,
    ) -> None:
        return None

    async def request_config_validation(
        self,
        *,
        client_id: str,
        config_apply_run_id: str,
    ) -> None:
        return None

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

"""Test-only support classes for runtime import-path validation."""

from __future__ import annotations

from typing import override

from mas_agent import Agent


class NoopAgent(Agent[dict[str, object]]):
    """Agent with no-op lifecycle for runner tests."""

    @override
    async def start(self) -> None:
        self._running = True

    @override
    async def stop(self) -> None:
        self._running = False


class AddressCaptureAgent(Agent[dict[str, object]]):
    """Agent used to assert server address normalization."""

    @override
    async def start(self) -> None:
        self._running = True

    @override
    async def stop(self) -> None:
        self._running = False


class NotAnAgent:
    """Dummy class for validation tests."""

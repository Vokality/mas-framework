"""Small background-task primitives for deferred Phase 3 execution."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any


class DurableTaskRunner:
    """Track detached tasks so deferred chat work can finish safely."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()

    def schedule(self, task: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        """Create and track one detached background task."""

        created = asyncio.create_task(task)
        self._tasks.add(created)
        created.add_done_callback(self._tasks.discard)
        return created

    async def drain(self) -> None:
        """Await all currently tracked tasks during shutdown."""

        if not self._tasks:
            return
        await asyncio.gather(*tuple(self._tasks), return_exceptions=True)


__all__ = ["DurableTaskRunner"]

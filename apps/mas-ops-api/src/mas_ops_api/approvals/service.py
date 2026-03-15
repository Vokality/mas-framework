"""Approval lifecycle orchestration and runtime expiry sweeping."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime

from mas_msp_core.approvals import (
    ApprovalCancellation,
    ApprovalController,
    ApprovalRecord,
)

from mas_ops_api.db.base import utc_now


logger = logging.getLogger(__name__)


class ApprovalService:
    """Coordinate approval lifecycle commands and hosted expiry sweeping."""

    def __init__(
        self,
        *,
        approval_controller: ApprovalController,
        expiry_poll_seconds: float,
    ) -> None:
        self._approval_controller = approval_controller
        self._expiry_poll_seconds = expiry_poll_seconds
        self._expiry_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def expire_pending(
        self,
        *,
        now: datetime | None = None,
    ) -> list[ApprovalRecord]:
        """Expire all pending approvals whose deadline has elapsed."""

        return await self._approval_controller.expire_pending(now=now or utc_now())

    async def cancel_request(
        self,
        *,
        cancellation: ApprovalCancellation,
    ) -> ApprovalRecord:
        """Cancel one approval request before execution begins."""

        return await self._approval_controller.cancel_request(cancellation)

    def start(self) -> None:
        """Start the hosted approval-expiry polling loop if enabled."""

        if self._expiry_poll_seconds <= 0 or self._expiry_task is not None:
            return
        self._stop_event.clear()
        self._expiry_task = asyncio.create_task(self._run_expiry_loop())

    async def stop(self) -> None:
        """Stop the hosted approval-expiry polling loop."""

        if self._expiry_task is None:
            return
        self._stop_event.set()
        self._expiry_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._expiry_task
        self._expiry_task = None
        self._stop_event = asyncio.Event()

    async def _run_expiry_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.expire_pending()
            except Exception:  # pragma: no cover - defensive background logging
                logger.exception("approval expiry sweep failed")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._expiry_poll_seconds,
                )
            except TimeoutError:
                continue


__all__ = ["ApprovalService"]

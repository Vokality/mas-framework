"""Performance tests for DLP scanning."""

import time

import pytest

from mas.gateway.dlp import DLPModule

pytestmark = pytest.mark.asyncio


class TestDlpPerformance:
    """Performance expectations for DLP scanning."""

    async def test_scan_latency_under_budget(self) -> None:
        """Ensure DLP scan stays within latency budget."""
        dlp = DLPModule()
        payload_text = (
            "User profile: John Doe, email user@example.com. "
            "Notes: SSN 123-45-6789. " + ("x" * 2000)
        )
        payload: dict[str, object] = {"message": payload_text}

        warmup_runs = 5
        measured_runs = 50

        for _ in range(warmup_runs):
            await dlp.scan(payload)

        start = time.perf_counter()
        for _ in range(measured_runs):
            await dlp.scan(payload)
        elapsed = time.perf_counter() - start

        avg_ms = (elapsed / measured_runs) * 1000
        assert avg_ms <= 5.0, f"Average DLP scan latency {avg_ms:.2f} ms exceeds 5 ms"

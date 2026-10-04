"""Run and report the strict sustained MAS acceptance gate."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from integration_tests.load_support import CapacityTarget, run_sustained_load


class LoadOptions(BaseModel):
    """Validated command inputs for the benchmark boundary."""

    duration: float = Field(default=60, gt=0, allow_inf_nan=False)
    rate: int | None = Field(default=None, gt=0)
    rate_per_cpu: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    p95_ms: float = Field(default=300, gt=0, allow_inf_nan=False)
    output: Path = Path("artifacts/production-load.json")
    diagnostics: bool = False
    broker_mode: Literal["process", "in_process"] = "process"

    @model_validator(mode="after")
    def _validate_diagnostics(self) -> LoadOptions:
        """Keep monkeypatch profiles explicit and outside isolated deployment."""
        if self.diagnostics and self.broker_mode != "in_process":
            raise ValueError("--diagnostics requires --broker-mode in_process")
        if self.rate is not None and self.rate_per_cpu is not None:
            raise ValueError("--rate and --rate-per-cpu are mutually exclusive")
        return self

    def capacity_target(self) -> CapacityTarget:
        """Capture usable CPUs once and resolve the configured workload budget."""
        return CapacityTarget.resolve(
            rate=self.rate,
            rate_per_cpu=self.rate_per_cpu,
            usable_cpu_count=os.process_cpu_count(),
        )


def parse_options(arguments: Sequence[str] | None = None) -> LoadOptions:
    """Parse the real CLI boundary with explicitly exclusive rate policies."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=60)
    rates = parser.add_mutually_exclusive_group()
    rates.add_argument("--rate", type=int, help="absolute messages/sec (default: 1000)")
    rates.add_argument(
        "--rate-per-cpu", type=float, help="messages/sec per usable CPU, rounded up"
    )
    parser.add_argument("--p95-ms", type=float, default=300)
    parser.add_argument("--diagnostics", action="store_true")
    parser.add_argument(
        "--broker-mode", choices=("process", "in_process"), default="process"
    )
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/production-load.json")
    )
    return LoadOptions.model_validate(vars(parser.parse_args(arguments)))


async def main() -> int:
    """Write measured JSON evidence and return a failing exit code on any gate."""
    args = parse_options()
    capacity = args.capacity_target()
    with TemporaryDirectory(prefix="mas-acceptance-") as directory:
        report = await run_sustained_load(
            Path(directory),
            duration=args.duration,
            target_rate=capacity.target_rate,
            capacity=capacity,
            latency_limit_ms=args.p95_ms,
            diagnostics=args.diagnostics,
            broker_mode=args.broker_mode,
        )
    payload = json.dumps(report.as_dict(), indent=2, sort_keys=True, allow_nan=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload + "\n")
    print(payload)
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

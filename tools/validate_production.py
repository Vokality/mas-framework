"""Run and report the strict sustained MAS acceptance gate."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from integration_tests.load_support import run_sustained_load


class LoadOptions(BaseModel):
    """Validated command inputs for the benchmark boundary."""

    duration: float = Field(default=60, gt=0, allow_inf_nan=False)
    rate: int = Field(default=1000, gt=0)
    p95_ms: float = Field(default=300, gt=0, allow_inf_nan=False)
    output: Path = Path("artifacts/production-load.json")
    diagnostics: bool = False
    broker_mode: Literal["process", "in_process"] = "process"

    @model_validator(mode="after")
    def _validate_diagnostics(self) -> LoadOptions:
        """Keep monkeypatch profiles explicit and outside isolated deployment."""
        if self.diagnostics and self.broker_mode != "in_process":
            raise ValueError("--diagnostics requires --broker-mode in_process")
        return self


async def main() -> int:
    """Write measured JSON evidence and return a failing exit code on any gate."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--rate", type=int, default=1000)
    parser.add_argument("--p95-ms", type=float, default=300)
    parser.add_argument("--diagnostics", action="store_true")
    parser.add_argument(
        "--broker-mode", choices=("process", "in_process"), default="process"
    )
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/production-load.json")
    )
    args = LoadOptions.model_validate(vars(parser.parse_args()))
    with TemporaryDirectory(prefix="mas-acceptance-") as directory:
        report = await run_sustained_load(
            Path(directory),
            duration=args.duration,
            target_rate=args.rate,
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

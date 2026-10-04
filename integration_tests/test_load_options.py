"""Redis-free workload policy and CLI reporting regressions."""

import json
import sys
from pathlib import Path
from typing import Literal

import pytest
from pydantic import TypeAdapter, ValidationError

from integration_tests.load_support import (
    CapacityTarget,
    LoadReport,
    run_sustained_load,
)
from tools import validate_production


def _report(capacity: CapacityTarget) -> LoadReport:
    data = TypeAdapter(dict[str, object]).validate_json(
        (Path(__file__).parents[1] / "artifacts/production-load.json").read_bytes()
    )
    data["capacity"] = capacity
    data["target_rate"] = capacity.target_rate
    return TypeAdapter(LoadReport).validate_python(data)


def test_default_remains_absolute_even_with_unknown_cpu_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(validate_production.os, "process_cpu_count", lambda: None)
    options = validate_production.parse_options([])
    assert options.capacity_target() == CapacityTarget(
        basis="absolute", target_rate=1000
    )
    assert options.p95_ms == 300


@pytest.mark.parametrize(("cpus", "expected"), [(4, 250), (16, 1000)])
def test_per_cpu_cli_uses_usable_count_not_system_cpu_count(
    monkeypatch: pytest.MonkeyPatch, cpus: int, expected: int
) -> None:
    monkeypatch.setattr(validate_production.os, "process_cpu_count", lambda: cpus)
    monkeypatch.setattr(validate_production.os, "cpu_count", lambda: 64)
    target = validate_production.parse_options(
        ["--rate-per-cpu", "62.5"]
    ).capacity_target()
    assert target == CapacityTarget(
        basis="per_cpu",
        target_rate=expected,
        rate_per_cpu=62.5,
        usable_cpu_count=cpus,
    )


def test_fractional_cpu_budget_rounds_up_without_reducing_requested_rate() -> None:
    assert (
        CapacityTarget.resolve(rate_per_cpu=62.6, usable_cpu_count=4).target_rate == 251
    )
    assert CapacityTarget.resolve(rate_per_cpu=0.1, usable_cpu_count=1).target_rate == 1


@pytest.mark.parametrize("cpus", [None, 0, -1])
def test_per_cpu_rejects_unknown_or_nonpositive_usable_count(
    monkeypatch: pytest.MonkeyPatch, cpus: int | None
) -> None:
    monkeypatch.setattr(validate_production.os, "process_cpu_count", lambda: cpus)
    with pytest.raises(ValueError, match="known positive usable CPU count"):
        validate_production.parse_options(["--rate-per-cpu", "62.5"]).capacity_target()


@pytest.mark.parametrize("rate", ["0", "-1", "nan", "inf", "-inf"])
def test_per_cpu_cli_rejects_nonpositive_and_nonfinite_rates(rate: str) -> None:
    with pytest.raises(ValidationError):
        validate_production.parse_options([f"--rate-per-cpu={rate}"])


def test_per_cpu_rejects_overflowing_scaled_budget() -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        CapacityTarget.resolve(rate_per_cpu=1e308, usable_cpu_count=4)


def test_rate_policies_are_exclusive_at_cli_and_domain_boundaries() -> None:
    with pytest.raises(SystemExit) as failure:
        validate_production.parse_options(["--rate", "1000", "--rate-per-cpu", "62.5"])
    assert failure.value.code == 2
    with pytest.raises(ValidationError, match="mutually exclusive"):
        validate_production.LoadOptions(rate=1000, rate_per_cpu=62.5)
    with pytest.raises(ValueError, match="mutually exclusive"):
        CapacityTarget.resolve(rate=1000, rate_per_cpu=62.5, usable_cpu_count=4)


@pytest.mark.parametrize(
    "data",
    [
        {
            "basis": "per_cpu",
            "target_rate": 249,
            "rate_per_cpu": 62.5,
            "usable_cpu_count": 4,
        },
        {"basis": "per_cpu", "target_rate": 250, "rate_per_cpu": 62.5},
        {"basis": "absolute", "target_rate": 1000, "rate_per_cpu": 62.5},
        {
            "basis": "per_cpu",
            "target_rate": 250,
            "rate_per_cpu": 1e308,
            "usable_cpu_count": 4,
        },
    ],
)
def test_capacity_metadata_rejects_inconsistent_domain_values(
    data: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        CapacityTarget.model_validate(data)


@pytest.mark.asyncio
async def test_report_target_mismatch_fails_before_creating_infrastructure(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="match the actual target rate"):
        await run_sustained_load(
            tmp_path,
            target_rate=1000,
            capacity=CapacityTarget.resolve(rate_per_cpu=62.5, usable_cpu_count=4),
        )
    assert not (tmp_path / "redis").exists()


@pytest.mark.asyncio
async def test_cli_passes_resolved_target_and_serializes_cohesive_capacity_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(validate_production.os, "process_cpu_count", lambda: 4)
    output = tmp_path / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["validate_production", "--rate-per-cpu", "62.5", "--output", str(output)],
    )
    target = CapacityTarget.resolve(rate_per_cpu=62.5, usable_cpu_count=4)

    async def measured(
        directory: Path,
        *,
        duration: float,
        target_rate: int,
        latency_limit_ms: float,
        diagnostics: bool,
        broker_mode: Literal["process", "in_process"],
        capacity: CapacityTarget | None,
    ) -> LoadReport:
        assert directory.is_dir()
        assert target_rate == 250 and capacity == target
        assert duration == 60 and latency_limit_ms == 300
        assert not diagnostics and broker_mode == "process"
        return _report(target)

    monkeypatch.setattr(validate_production, "run_sustained_load", measured)
    assert await validate_production.main() == 0
    data = TypeAdapter(dict[str, object]).validate_json(output.read_bytes())
    assert data["target_rate"] == 250
    assert data["capacity"] == {
        "basis": "per_cpu",
        "target_rate": 250,
        "rate_per_cpu": 62.5,
        "usable_cpu_count": 4,
    }
    assert data["latency_limit_ms"] == 300
    assert data["sample_ratio"] == 1
    assert data["wait_for_aof"] is True
    assert data["required_replica_confirmations"] == 1
    json.dumps(data, allow_nan=False)

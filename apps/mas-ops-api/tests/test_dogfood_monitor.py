"""Tests for Docker-backed MAS dogfood monitoring."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from mas_msp_contracts import HealthState
from mas_msp_hosts import LinuxPollObservation
from mas_ops_api.db.models import PortfolioAsset, PortfolioClient, PortfolioIncident
from mas_ops_api.dogfood import DockerDogfoodMonitorService


@dataclass(slots=True)
class FakeHostPoller:
    observations: list[LinuxPollObservation]

    async def poll(self, target) -> LinuxPollObservation:  # noqa: ANN001
        del target
        if not self.observations:
            raise AssertionError("no observations left")
        return self.observations.pop(0)


@pytest.mark.asyncio
async def test_dogfood_monitor_projects_runtime_once_per_problem_signature(
    ops_app,
    session_factory,
) -> None:
    settings = ops_app.state.services.settings.model_copy(
        update={
            "dogfood_enabled": True,
            "dogfood_hostname": "mas-runtime",
            "dogfood_mgmt_address": "docker://mas-runtime",
            "dogfood_distribution": "Docker Linux",
            "dogfood_site": "docker-compose",
            "dogfood_tags": ["docker", "mas-system"],
        }
    )
    service = DockerDogfoodMonitorService(
        settings=settings,
        portfolio_ingress_registry=ops_app.state.services.portfolio_ingress_registry,
        poller=FakeHostPoller(
            observations=[
                LinuxPollObservation(
                    collected_at=datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
                    metrics={"container_status": "running"},
                    services=[
                        {
                            "service_name": "mas-runtime",
                            "service_state": "unhealthy",
                        }
                    ],
                    findings=[{"code": "container_unhealthy"}],
                ),
                LinuxPollObservation(
                    collected_at=datetime(2026, 3, 15, 12, 1, tzinfo=UTC),
                    metrics={"container_status": "running"},
                    services=[
                        {
                            "service_name": "mas-runtime",
                            "service_state": "unhealthy",
                        }
                    ],
                    findings=[{"code": "container_unhealthy"}],
                ),
            ]
        ),
    )

    await service._poll_once()  # noqa: SLF001
    await service._poll_once()  # noqa: SLF001

    async with session_factory() as session:
        client = await session.get(PortfolioClient, settings.dogfood_client_id)
        assets = list((await session.scalars(select(PortfolioAsset))).all())
        incidents = list((await session.scalars(select(PortfolioIncident))).all())

    assert client is not None
    assert client.open_alert_count == 1
    assert len(assets) == 1
    assert assets[0].asset_kind == "linux_host"
    assert assets[0].health_state == HealthState.CRITICAL.value
    assert len(incidents) == 1
    assert (
        incidents[0].summary
        == "mas-runtime on mas-runtime: mas-runtime container reported unhealthy status"
    )

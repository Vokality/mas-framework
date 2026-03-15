"""Test fixtures for the ops-plane API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mas_msp_contracts import (
    ApprovalState,
    AssetKind,
    HealthState,
    IncidentState,
    Severity,
)
from mas_ops_api import create_app
from mas_ops_api.auth.types import UserRole
from mas_ops_api.db.base import utc_now
from mas_ops_api.db.models import (
    ApprovalRequestRecord,
    ConfigDesiredStateRecord,
    OpsSession,
    OpsUser,
    OpsUserClientAccess,
    OpsUserPassword,
    PortfolioActivityEvent,
    PortfolioAsset,
    PortfolioClient,
    PortfolioIncident,
)
from mas_ops_api.settings import OpsApiSettings


CLIENT_A = "11111111-1111-4111-8111-111111111111"
CLIENT_B = "22222222-2222-4222-8222-222222222222"
FABRIC_A = "33333333-3333-4333-8333-333333333333"
FABRIC_B = "44444444-4444-4444-8444-444444444444"
INCIDENT_A = "55555555-5555-4555-8555-555555555555"
INCIDENT_B = "66666666-6666-4666-8666-666666666666"
ASSET_A = "77777777-7777-4777-8777-777777777777"
ASSET_B = "88888888-8888-4888-8888-888888888888"
APPROVAL_A = "99999999-9999-4999-8999-999999999999"


@pytest.fixture
async def ops_app(tmp_path: Path):
    """Create the FastAPI app with a temporary SQLite database."""

    settings = OpsApiSettings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'ops-api.db'}",
        auto_create_schema=True,
        environment="test",
        public_base_url="http://localhost:8080",
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def api_client(ops_app) -> AsyncIterator[AsyncClient]:
    """HTTP client bound directly to the FastAPI ASGI app."""

    transport = ASGITransport(app=ops_app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=True,
    ) as client:
        yield client


@pytest.fixture
def session_factory(ops_app) -> async_sessionmaker[AsyncSession]:
    """Return the SQLAlchemy async session factory."""

    return ops_app.state.services.database.session_factory


@pytest.fixture
def seed_user(ops_app, session_factory):
    """Seed a human user and optional client access grants."""

    async def create(
        *,
        email: str,
        password: str,
        role: UserRole,
        client_ids: tuple[str, ...] = (),
        display_name: str = "Test User",
    ) -> OpsUser:
        async with session_factory() as session:
            user = OpsUser(
                user_id=str(uuid4()),
                email=email.lower(),
                display_name=display_name,
                role=role.value,
            )
            session.add(user)
            session.add(
                OpsUserPassword(
                    user_id=user.user_id,
                    password_hash=ops_app.state.services.auth_service.password_service.hash_password(
                        password
                    ),
                )
            )
            for client_id in client_ids:
                session.add(
                    OpsUserClientAccess(user_id=user.user_id, client_id=client_id)
                )
            await session.commit()
            return user

    return create


@pytest.fixture
def seed_portfolio(session_factory):
    """Seed two clients and representative Phase 1 portfolio data."""

    async def create() -> None:
        now = utc_now()
        async with session_factory() as session:
            session.add_all(
                [
                    PortfolioClient(
                        client_id=CLIENT_A,
                        fabric_id=FABRIC_A,
                        name="Acme Corp",
                        open_alert_count=3,
                        critical_asset_count=1,
                        updated_at=now,
                    ),
                    PortfolioClient(
                        client_id=CLIENT_B,
                        fabric_id=FABRIC_B,
                        name="Beta Corp",
                        open_alert_count=1,
                        critical_asset_count=0,
                        updated_at=now,
                    ),
                    PortfolioIncident(
                        incident_id=INCIDENT_A,
                        client_id=CLIENT_A,
                        fabric_id=FABRIC_A,
                        state=IncidentState.INVESTIGATING.value,
                        severity=Severity.MAJOR.value,
                        summary="Primary uplink is unstable",
                        opened_at=now - timedelta(hours=1),
                        updated_at=now,
                    ),
                    PortfolioIncident(
                        incident_id=INCIDENT_B,
                        client_id=CLIENT_B,
                        fabric_id=FABRIC_B,
                        state=IncidentState.OPEN.value,
                        severity=Severity.WARNING.value,
                        summary="WAN latency is elevated",
                        opened_at=now - timedelta(hours=2),
                        updated_at=now,
                    ),
                    PortfolioAsset(
                        asset_id=ASSET_A,
                        client_id=CLIENT_A,
                        fabric_id=FABRIC_A,
                        asset_kind=AssetKind.NETWORK_DEVICE.value,
                        vendor="Cisco",
                        model="Catalyst 9300",
                        hostname="edge-sw-01",
                        mgmt_address="10.0.0.10",
                        site="nyc-1",
                        tags=["core"],
                        health_state=HealthState.DEGRADED.value,
                        updated_at=now,
                    ),
                    PortfolioAsset(
                        asset_id=ASSET_B,
                        client_id=CLIENT_B,
                        fabric_id=FABRIC_B,
                        asset_kind=AssetKind.NETWORK_DEVICE.value,
                        vendor="Fortinet",
                        model="FortiGate 100F",
                        hostname="fw-01",
                        mgmt_address="10.10.0.1",
                        site="bos-1",
                        tags=["edge"],
                        health_state=HealthState.HEALTHY.value,
                        updated_at=now,
                    ),
                    PortfolioActivityEvent(
                        source_event_id="activity-a-1",
                        client_id=CLIENT_A,
                        fabric_id=FABRIC_A,
                        incident_id=INCIDENT_A,
                        event_type="incident.updated",
                        subject_type="incident",
                        subject_id=INCIDENT_A,
                        payload={"summary": "Investigation started"},
                        occurred_at=now,
                    ),
                    ApprovalRequestRecord(
                        approval_id=APPROVAL_A,
                        client_id=CLIENT_A,
                        fabric_id=FABRIC_A,
                        incident_id=INCIDENT_A,
                        state=ApprovalState.PENDING.value,
                        action_kind="network.remediation",
                        title="Bounce primary uplink",
                        requested_at=now,
                        expires_at=now + timedelta(hours=1),
                        requested_by_agent="core-orchestrator",
                        payload={"action_type": "interface.shutdown_no_shutdown"},
                        risk_summary="May briefly impact the primary uplink.",
                    ),
                    ConfigDesiredStateRecord(
                        client_id=CLIENT_A,
                        fabric_id=FABRIC_A,
                        desired_state_version=1,
                        tenant_metadata={"display_name": "Acme Corp"},
                        policy={"default_mode": "deny"},
                        inventory_sources=[{"kind": "snmp"}],
                        notification_routes=[
                            {"kind": "email", "target": "noc@example.com"}
                        ],
                        updated_at=now,
                    ),
                ]
            )
            await session.commit()

    return create


@pytest.fixture
def login(api_client: AsyncClient):
    """Authenticate a seeded user and preserve the issued session cookie."""

    async def run(email: str, password: str):
        return await api_client.post(
            "/auth/login",
            json={"email": email, "password": password},
        )

    return run


@pytest.fixture
def session_cookie_name(ops_app) -> str:
    """Expose the configured session cookie name."""

    return ops_app.state.services.settings.session_cookie_name


@pytest.fixture
def insert_stream_events(ops_app, session_factory):
    """Insert committed SSE events directly into the stream log."""

    async def create(events: list[dict[str, Any]]) -> list[int]:
        stream_service = ops_app.state.services.stream_service
        stream_ids: list[int] = []
        async with session_factory() as session:
            rows = []
            for event in events:
                row = stream_service.build_event(**event)
                session.add(row)
                rows.append(row)
            await session.commit()
            for row in rows:
                await session.refresh(row)
                stream_ids.append(row.stream_id)
        return stream_ids

    return create


async def expire_current_session(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    idle_delta: timedelta | None = None,
    absolute_delta: timedelta | None = None,
) -> None:
    """Expire the latest session to exercise timeout behavior."""

    async with session_factory() as session:
        session_rows = list(
            (
                await session.scalars(
                    select(OpsSession).order_by(OpsSession.created_at.desc())
                )
            ).all()
        )
        for session_row in session_rows:
            if idle_delta is not None:
                session_row.idle_expires_at = datetime.now(UTC) - idle_delta
            if absolute_delta is not None:
                session_row.absolute_expires_at = datetime.now(UTC) - absolute_delta
        await session.commit()

"""Tests for explicit client enrollment bootstrap helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from mas_ops_api.bootstrap import ClientBootstrapConfig, ensure_client_enrollment
from mas_ops_api.db.bootstrap import create_schema
from mas_ops_api.db.models import (
    ConfigDesiredStateRecord,
    OpsAuditEntry,
    OpsStreamEvent,
    PortfolioClient,
)
from mas_ops_api.db.session import Database
from mas_ops_api.settings import OpsApiSettings


@pytest.mark.asyncio
async def test_ensure_client_enrollment_is_idempotent(tmp_path: Path) -> None:
    settings = OpsApiSettings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'bootstrap.db'}",
        auto_create_schema=False,
        environment="test",
        public_base_url="http://localhost:8080",
    )
    database = Database(settings)
    await create_schema(database.engine)
    try:
        config = ClientBootstrapConfig(
            client_id="11111111-1111-4111-8111-111111111111",
            fabric_id="33333333-3333-4333-8333-333333333333",
            display_name="MAS Dogfood",
        )

        await ensure_client_enrollment(settings, config=config)
        await ensure_client_enrollment(settings, config=config)

        async with database.session_factory() as session:
            client = await session.get(PortfolioClient, config.client_id)
            desired_state = await session.get(
                ConfigDesiredStateRecord,
                config.client_id,
            )
            client_count = await session.scalar(
                select(func.count()).select_from(PortfolioClient)
            )
            desired_state_count = await session.scalar(
                select(func.count()).select_from(ConfigDesiredStateRecord)
            )
            audit_count = await session.scalar(
                select(func.count()).select_from(OpsAuditEntry)
            )
            stream_count = await session.scalar(
                select(func.count()).select_from(OpsStreamEvent)
            )

        assert client_count == 1
        assert desired_state_count == 1
        assert audit_count == 1
        assert stream_count == 2
        assert client is not None
        assert client.name == "MAS Dogfood"
        assert desired_state is not None
        assert desired_state.fabric_id == config.fabric_id
        assert desired_state.tenant_metadata == {"display_name": "MAS Dogfood"}
        assert desired_state.policy == {}
        assert desired_state.inventory_sources == []
        assert desired_state.notification_routes == []
    finally:
        await database.dispose()

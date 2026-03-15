"""Tests for admin bootstrap helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from mas_ops_api.bootstrap import AdminBootstrapConfig, ensure_admin_user
from mas_ops_api.db.bootstrap import create_schema
from mas_ops_api.db.models import OpsUser, OpsUserPassword, PortfolioClient
from mas_ops_api.db.session import Database
from mas_ops_api.settings import OpsApiSettings


@pytest.mark.asyncio
async def test_ensure_admin_user_is_idempotent(tmp_path: Path) -> None:
    settings = OpsApiSettings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'bootstrap.db'}",
        auto_create_schema=False,
        environment="test",
        public_base_url="http://localhost:8080",
    )
    database = Database(settings)
    await create_schema(database.engine)
    try:
        await ensure_admin_user(
            settings,
            config=AdminBootstrapConfig(
                email="admin@example.com",
                password="admin123",
                display_name="Compose Admin",
            ),
        )
        await ensure_admin_user(
            settings,
            config=AdminBootstrapConfig(
                email="admin@example.com",
                password="admin123",
                display_name="Compose Admin",
            ),
        )

        async with database.session_factory() as session:
            assert (
                await session.scalar(select(func.count()).select_from(OpsUser))
            ) == 1
            assert (
                await session.scalar(select(func.count()).select_from(OpsUserPassword))
            ) == 1
            assert (
                await session.scalar(select(func.count()).select_from(PortfolioClient))
            ) == 0
    finally:
        await database.dispose()

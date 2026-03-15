"""Database engine regression tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from mas_ops_api.db.session import Database
from mas_ops_api.settings import OpsApiSettings


@pytest.mark.asyncio
async def test_sqlite_connections_enable_foreign_keys(tmp_path: Path) -> None:
    settings = OpsApiSettings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'ops-api.db'}",
        auto_create_schema=False,
        environment="test",
        public_base_url="http://localhost:8080",
    )
    database = Database(settings)

    try:
        async with database.engine.connect() as connection:
            pragma_value = (
                await connection.execute(text("PRAGMA foreign_keys"))
            ).scalar_one()
    finally:
        await database.dispose()

    assert pragma_value == 1

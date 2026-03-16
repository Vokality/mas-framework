"""Regression tests for ops-plane migration wiring."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from mas_ops_api import create_app
from mas_ops_api.db.bootstrap import create_schema
from mas_ops_api.db.migrations import (
    _build_alembic_config,
    assert_database_at_head,
    upgrade_to_head,
)
from mas_ops_api.db.session import Database
from mas_ops_api.settings import OpsApiSettings


def test_alembic_config_preserves_psycopg_driver() -> None:
    settings = OpsApiSettings(
        database_url="postgresql+psycopg://postgres:postgres@postgres:5432/mas_ops"
    )

    config = _build_alembic_config(settings)

    assert config.get_main_option("sqlalchemy.url") == settings.database_url


def test_alembic_config_normalizes_aiosqlite_driver() -> None:
    settings = OpsApiSettings(database_url="sqlite+aiosqlite:////tmp/ops-api.db")

    config = _build_alembic_config(settings)

    assert config.get_main_option("sqlalchemy.url") == "sqlite:////tmp/ops-api.db"


def test_upgrade_to_head_creates_alerting_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "ops-api-migrations.db"
    settings = OpsApiSettings(database_url=f"sqlite:///{database_path}")

    upgrade_to_head(settings)
    assert_database_at_head(settings)

    engine = create_engine(settings.database_url)
    inspector = inspect(engine)
    assert "applied_alert_policies" in inspector.get_table_names()
    assert "alert_condition_states" in inspector.get_table_names()
    incident_columns = {
        column["name"] for column in inspector.get_columns("portfolio_incidents")
    }
    assert "correlation_key" in incident_columns
    incident_indexes = {
        index["name"] for index in inspector.get_indexes("portfolio_incidents")
    }
    assert "ix_portfolio_incidents_correlation_key" in incident_indexes


@pytest.mark.asyncio
async def test_app_startup_rejects_unversioned_schema(tmp_path: Path) -> None:
    settings = OpsApiSettings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'stale.db'}",
        auto_create_schema=False,
        environment="test",
        public_base_url="http://localhost:8080",
    )
    database = Database(settings)
    await create_schema(database.engine)
    await database.dispose()

    app = create_app(settings)

    with pytest.raises(RuntimeError, match="database schema is not at Alembic head"):
        async with app.router.lifespan_context(app):
            pass


@pytest.mark.asyncio
async def test_app_startup_auto_create_schema_runs_migrations(tmp_path: Path) -> None:
    database_path = tmp_path / "ops-api-startup.db"
    settings = OpsApiSettings(
        database_url=f"sqlite+aiosqlite:///{database_path}",
        auto_create_schema=True,
        environment="test",
        public_base_url="http://localhost:8080",
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        pass

    engine = create_engine(f"sqlite:///{database_path}")
    inspector = inspect(engine)
    assert "alembic_version" in inspector.get_table_names()

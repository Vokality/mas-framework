"""Regression tests for ops-plane migration wiring."""

from __future__ import annotations

from mas_ops_api.db.migrations import _build_alembic_config
from mas_ops_api.settings import OpsApiSettings


def test_alembic_config_preserves_psycopg_driver() -> None:
    settings = OpsApiSettings(
        database_url="postgresql+psycopg://postgres:postgres@postgres:5432/mas_ops"
    )

    config = _build_alembic_config(settings)

    assert config.get_main_option("sqlalchemy.url") == settings.database_url

"""Programmatic Alembic helpers for the ops-plane database."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from mas_ops_api.settings import OpsApiSettings


def upgrade_to_head(settings: OpsApiSettings | None = None) -> None:
    """Apply all pending ops-plane migrations."""

    command.upgrade(_build_alembic_config(settings or OpsApiSettings()), "head")


def _build_alembic_config(settings: OpsApiSettings) -> Config:
    app_dir = Path(__file__).resolve().parents[3]
    config = Config(str(app_dir / "alembic.ini"))
    config.set_main_option("script_location", str(app_dir / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    return config


__all__ = ["upgrade_to_head"]

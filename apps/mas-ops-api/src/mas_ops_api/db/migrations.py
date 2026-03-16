"""Programmatic Alembic helpers for the ops-plane database."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from mas_ops_api.settings import OpsApiSettings


@dataclass(frozen=True, slots=True)
class SchemaVersionStatus:
    """Current database revision state compared with Alembic head."""

    current_revisions: tuple[str, ...]
    head_revisions: tuple[str, ...]

    @property
    def at_head(self) -> bool:
        """Return whether the database is already at Alembic head."""

        return self.current_revisions == self.head_revisions


def upgrade_to_head(settings: OpsApiSettings | None = None) -> None:
    """Apply all pending ops-plane migrations."""

    command.upgrade(_build_alembic_config(settings or OpsApiSettings()), "head")


def get_schema_version_status(
    settings: OpsApiSettings | None = None,
) -> SchemaVersionStatus:
    """Return the current database revision state compared with Alembic head."""

    config = _build_alembic_config(settings or OpsApiSettings())
    script = ScriptDirectory.from_config(config)
    head_revisions = tuple(sorted(script.get_heads()))
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    try:
        with engine.connect() as connection:
            current_revisions = tuple(
                sorted(MigrationContext.configure(connection).get_current_heads())
            )
    finally:
        engine.dispose()
    return SchemaVersionStatus(
        current_revisions=current_revisions,
        head_revisions=head_revisions,
    )


def assert_database_at_head(
    settings: OpsApiSettings | None = None,
) -> SchemaVersionStatus:
    """Raise when the database schema does not match Alembic head."""

    status = get_schema_version_status(settings)
    if status.at_head:
        return status
    current = ", ".join(status.current_revisions) if status.current_revisions else "<unversioned>"
    head = ", ".join(status.head_revisions)
    raise RuntimeError(
        f"database schema is not at Alembic head (current={current}, head={head})"
    )


def _build_alembic_config(settings: OpsApiSettings) -> Config:
    app_dir = Path(__file__).resolve().parents[3]
    config = Config(str(app_dir / "alembic.ini"))
    config.set_main_option("script_location", str(app_dir / "alembic"))
    config.set_main_option("sqlalchemy.url", _alembic_database_url(settings.database_url))
    return config


def _alembic_database_url(database_url: str) -> str:
    url = make_url(database_url)
    if url.drivername == "sqlite+aiosqlite":
        return url.set(drivername="sqlite").render_as_string(hide_password=False)
    return database_url


__all__ = [
    "SchemaVersionStatus",
    "assert_database_at_head",
    "get_schema_version_status",
    "upgrade_to_head",
]

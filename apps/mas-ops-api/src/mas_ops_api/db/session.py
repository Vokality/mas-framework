"""Database engine and session helpers for the ops plane."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from mas_ops_api.settings import OpsApiSettings


class Database:
    """Own the SQLAlchemy engine and async session factory."""

    def __init__(self, settings: OpsApiSettings) -> None:
        connect_args: dict[str, object] = {}
        if settings.database_url.startswith("sqlite+aiosqlite://"):
            connect_args["check_same_thread"] = False
        self.engine: AsyncEngine = create_async_engine(
            settings.database_url,
            future=True,
            connect_args=connect_args,
        )
        if settings.database_url.startswith("sqlite+aiosqlite://"):
            event.listen(self.engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield an async SQLAlchemy session."""

        async with self.session_factory() as session:
            yield session

    async def dispose(self) -> None:
        """Dispose the underlying SQLAlchemy engine."""

        await self.engine.dispose()


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
    """Enable SQLite foreign-key enforcement for every new connection."""

    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


__all__ = ["Database"]

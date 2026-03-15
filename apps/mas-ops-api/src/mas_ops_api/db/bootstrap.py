"""Database bootstrap helpers."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine

from mas_ops_api.db.base import Base


async def create_schema(engine: AsyncEngine) -> None:
    """Create all ops-plane tables."""

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_schema(engine: AsyncEngine) -> None:
    """Drop all ops-plane tables."""

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


__all__ = ["create_schema", "drop_schema"]

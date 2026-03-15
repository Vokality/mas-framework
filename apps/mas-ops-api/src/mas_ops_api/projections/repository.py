"""Read-model queries for portfolio, client, incident, and asset views."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mas_ops_api.db.models import (
    ChatSession,
    PortfolioActivityEvent,
    PortfolioAsset,
    PortfolioClient,
    PortfolioIncident,
)


class PortfolioQueries:
    """Database-backed portfolio read-model queries."""

    @staticmethod
    async def list_clients(
        session: AsyncSession,
        *,
        allowed_client_ids: frozenset[str],
        admin: bool,
    ) -> list[PortfolioClient]:
        stmt = select(PortfolioClient).order_by(PortfolioClient.name.asc())
        if not admin:
            stmt = stmt.where(PortfolioClient.client_id.in_(allowed_client_ids))
        return list((await session.scalars(stmt)).all())

    @staticmethod
    async def get_client(
        session: AsyncSession, client_id: str
    ) -> PortfolioClient | None:
        return await session.get(PortfolioClient, client_id)

    @staticmethod
    async def list_incidents_for_client(
        session: AsyncSession,
        client_id: str,
    ) -> list[PortfolioIncident]:
        stmt = (
            select(PortfolioIncident)
            .where(PortfolioIncident.client_id == client_id)
            .order_by(PortfolioIncident.updated_at.desc())
        )
        return list((await session.scalars(stmt)).all())

    @staticmethod
    async def get_incident(
        session: AsyncSession,
        incident_id: str,
    ) -> PortfolioIncident | None:
        return await session.get(PortfolioIncident, incident_id)

    @staticmethod
    async def list_activity_for_incident(
        session: AsyncSession,
        incident_id: str,
    ) -> list[PortfolioActivityEvent]:
        stmt = (
            select(PortfolioActivityEvent)
            .where(PortfolioActivityEvent.incident_id == incident_id)
            .order_by(PortfolioActivityEvent.occurred_at.asc())
        )
        return list((await session.scalars(stmt)).all())

    @staticmethod
    async def list_assets_for_client(
        session: AsyncSession,
        client_id: str,
    ) -> list[PortfolioAsset]:
        stmt = (
            select(PortfolioAsset)
            .where(PortfolioAsset.client_id == client_id)
            .order_by(PortfolioAsset.hostname.asc(), PortfolioAsset.asset_id.asc())
        )
        return list((await session.scalars(stmt)).all())

    @staticmethod
    async def list_activity_for_client(
        session: AsyncSession,
        client_id: str,
    ) -> list[PortfolioActivityEvent]:
        stmt = (
            select(PortfolioActivityEvent)
            .where(PortfolioActivityEvent.client_id == client_id)
            .order_by(
                PortfolioActivityEvent.occurred_at.desc(),
                PortfolioActivityEvent.activity_id.desc(),
            )
        )
        return list((await session.scalars(stmt)).all())

    @staticmethod
    async def get_asset(session: AsyncSession, asset_id: str) -> PortfolioAsset | None:
        return await session.get(PortfolioAsset, asset_id)

    @staticmethod
    async def list_activity_for_asset(
        session: AsyncSession,
        asset_id: str,
    ) -> list[PortfolioActivityEvent]:
        stmt = (
            select(PortfolioActivityEvent)
            .where(PortfolioActivityEvent.asset_id == asset_id)
            .order_by(
                PortfolioActivityEvent.occurred_at.desc(),
                PortfolioActivityEvent.activity_id.desc(),
            )
        )
        return list((await session.scalars(stmt)).all())

    @staticmethod
    async def get_chat_session(
        session: AsyncSession,
        chat_session_id: str,
    ) -> ChatSession | None:
        return await session.get(ChatSession, chat_session_id)


__all__ = ["PortfolioQueries"]

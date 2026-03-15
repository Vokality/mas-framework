"""SQLAlchemy base objects and shared database helpers."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import DeclarativeBase


def utc_now() -> datetime:
    """Return the current UTC time."""

    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base for ops-plane persistence models."""


__all__ = ["Base", "utc_now"]

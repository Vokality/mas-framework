"""Custom SQLAlchemy types for ops-plane persistence."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.types import DateTime, TypeDecorator


class UtcDateTime(TypeDecorator[datetime]):
    """Timezone-aware datetime that normalizes all values to UTC."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(
        self,
        value: datetime | None,
        dialect,  # noqa: ANN001
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime values must be timezone-aware")
        return value.astimezone(UTC)

    def process_result_value(
        self,
        value: datetime | None,
        dialect,  # noqa: ANN001
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


__all__ = ["UtcDateTime"]

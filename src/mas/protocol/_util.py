from datetime import UTC, datetime
from typing import Any


def utc_now() -> datetime:
    return datetime.now(UTC)


def empty_dict() -> dict[str, Any]:
    return {}


def empty_list() -> list[Any]:
    return []

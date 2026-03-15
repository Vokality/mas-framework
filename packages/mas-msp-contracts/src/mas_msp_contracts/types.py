"""Shared scalar and structured types for MSP contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, TypeAlias, TypeAliasType
from uuid import UUID

from pydantic import AfterValidator, PlainSerializer, StringConstraints


JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue = TypeAliasType(
    "JSONValue",
    JSONScalar | list["JSONValue"] | dict[str, "JSONValue"],
)
JSONObject: TypeAlias = dict[str, JSONValue]

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def validate_uuid4_string(value: str) -> str:
    """Validate a lowercase hyphenated UUIDv4 string."""

    try:
        parsed = UUID(value)
    except ValueError as exc:  # pragma: no cover - pydantic surfaces the message
        raise ValueError("value must be a valid UUID string") from exc

    if parsed.version != 4:
        raise ValueError("value must be a UUIDv4 string")
    if str(parsed) != value:
        raise ValueError("value must be lowercase and hyphenated")
    return value


UUID4Str = Annotated[str, AfterValidator(validate_uuid4_string)]

UTC = timezone.utc


def validate_utc_datetime(value: datetime) -> datetime:
    """Require timezone-aware datetimes normalized to UTC."""

    offset = value.utcoffset()
    if offset is None:
        raise ValueError("value must be timezone-aware")
    if offset != timedelta(0):
        raise ValueError("value must use the UTC offset")
    return value.astimezone(UTC)


def serialize_utc_datetime(value: datetime) -> str:
    """Serialize UTC datetimes with a trailing Z suffix."""

    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


UTCDatetime = Annotated[
    datetime,
    AfterValidator(validate_utc_datetime),
    PlainSerializer(serialize_utc_datetime, return_type=str, when_used="json"),
]

AlertId = UUID4Str
ApprovalId = UUID4Str
AssetId = UUID4Str
ChatSessionId = UUID4Str
ClientId = UUID4Str
ConfigApplyRunId = UUID4Str
EventId = UUID4Str
EvidenceBundleId = UUID4Str
FabricId = UUID4Str
IncidentId = UUID4Str
RequestId = UUID4Str
SnapshotId = UUID4Str
TurnId = UUID4Str

__all__ = [
    "AlertId",
    "ApprovalId",
    "AssetId",
    "ChatSessionId",
    "ClientId",
    "ConfigApplyRunId",
    "EventId",
    "EvidenceBundleId",
    "FabricId",
    "IncidentId",
    "JSONObject",
    "JSONScalar",
    "JSONValue",
    "NonEmptyStr",
    "RequestId",
    "SnapshotId",
    "TurnId",
    "UTCDatetime",
    "UTC",
    "UUID4Str",
    "serialize_utc_datetime",
    "validate_utc_datetime",
    "validate_uuid4_string",
]

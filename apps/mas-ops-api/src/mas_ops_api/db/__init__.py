"""Database models, sessions, and migrations for the ops plane."""

from .base import Base, utc_now
from .bootstrap import create_schema, drop_schema
from .models import (
    ApprovalRequestRecord,
    ChatMessage,
    ChatSession,
    ChatTurn,
    ConfigApplyRun,
    ConfigDesiredStateRecord,
    ConfigValidationRun,
    OpsSession,
    OpsStreamEvent,
    OpsUser,
    OpsUserClientAccess,
    OpsUserPassword,
    PortfolioActivityEvent,
    PortfolioAsset,
    PortfolioClient,
    PortfolioIncident,
)
from .session import Database

__all__ = [
    "ApprovalRequestRecord",
    "Base",
    "ChatMessage",
    "ChatSession",
    "ChatTurn",
    "ConfigApplyRun",
    "ConfigDesiredStateRecord",
    "ConfigValidationRun",
    "Database",
    "OpsSession",
    "OpsStreamEvent",
    "OpsUser",
    "OpsUserClientAccess",
    "OpsUserPassword",
    "PortfolioActivityEvent",
    "PortfolioAsset",
    "PortfolioClient",
    "PortfolioIncident",
    "create_schema",
    "drop_schema",
    "utc_now",
]

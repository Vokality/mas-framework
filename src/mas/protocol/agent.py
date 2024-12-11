from datetime import UTC, datetime
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from .types import AgentStatus


def datetime_utc_now():
    return datetime.now(UTC)


class Agent(BaseModel):
    """Agent data model."""

    id: str
    token: str
    status: AgentStatus
    capabilities: List[str]
    metadata: Dict[str, Any]
    last_seen: datetime = Field(default_factory=datetime_utc_now)


class AgentRuntimeMetric(BaseModel):
    num_errors: int = Field(default=0)
    messages_sent: int = Field(default=0)
    messages_received: int = Field(default=0)

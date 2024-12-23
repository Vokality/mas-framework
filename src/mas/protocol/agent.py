from datetime import datetime
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from ._util import empty_dict, empty_list, utc_now
from .types import AgentStatus


class Agent(BaseModel):
    """Agent data model."""

    id: str
    token: str
    status: AgentStatus
    capabilities: List[str] = Field(default_factory=empty_list)
    metadata: Dict[str, Any] = Field(default_factory=empty_dict)
    last_seen: datetime = Field(default_factory=utc_now)


class AgentRuntimeMetric(BaseModel):
    num_errors: int = Field(default=0)
    messages_sent: int = Field(default=0)
    messages_received: int = Field(default=0)

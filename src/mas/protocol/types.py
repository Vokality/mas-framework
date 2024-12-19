from enum import Enum


class MessageStatus(str, Enum):
    """Message status."""

    FAILED = "failed"
    PENDING = "pending"
    REJECTED = "rejected"
    DELIVERED = "delivered"


class MessageType(str, Enum):
    """Core message types."""

    # Basic types
    ERROR = "error"
    STATUS_UPDATE = "status.update"
    STATUS_UPDATE_RESPONSE = "status.update.response"

    # Registration
    REGISTRATION_REQUEST = "registration.request"
    REGISTRATION_RESPONSE = "registration.response"
    DEREGISTRATION_REQUEST = "deregistration.request"
    DEREGISTRATION_RESPONSE = "deregistration.response"

    # Agent messages
    AGENT_MESSAGE = "agent.message"
    DISCOVERY_REQUEST = "discovery.request"
    DISCOVERY_RESPONSE = "discovery.response"

    # Health
    HEALTH_CHECK = "health.check"
    HEALTH_CHECK_RESPONSE = "health.check.response"


class AgentStatus(str, Enum):
    """Simplified agent status."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    SHUTDOWN = "shutdown"

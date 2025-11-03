"""Gateway Pattern implementation for MAS Framework."""

from .authentication import AuthenticationModule, AuthResult
from .authorization import AuthorizationModule
from .audit import AuditModule, AuditEntry
from .rate_limit import RateLimitModule, RateLimitResult
from .dlp import DLPModule, ScanResult, Violation, ViolationType, ActionPolicy
from .circuit_breaker import (
    CircuitBreakerModule,
    CircuitState,
    CircuitStatus,
    CircuitBreakerConfig,
)
from .priority_queue import (
    PriorityQueueModule,
    MessagePriority,
    QueuedMessage,
    EnqueueResult,
    DequeueResult,
    PriorityQueueConfig,
)
from .metrics import MetricsCollector, get_metrics, get_content_type
from .message_signing import MessageSigningModule, SignatureResult
from .config import (
    GatewaySettings,
    RedisSettings,
    RateLimitSettings,
    FeaturesSettings,
    CircuitBreakerSettings,
    PriorityQueueSettings,
    MessageSigningSettings,
    load_settings,
)
from .gateway import GatewayService, GatewayResult
from .auth_manager import AuthorizationManager

__all__ = [
    "AuthenticationModule",
    "AuthResult",
    "AuthorizationModule",
    "AuditModule",
    "AuditEntry",
    "RateLimitModule",
    "RateLimitResult",
    "DLPModule",
    "ScanResult",
    "Violation",
    "ViolationType",
    "ActionPolicy",
    "CircuitBreakerModule",
    "CircuitState",
    "CircuitStatus",
    "CircuitBreakerConfig",
    "PriorityQueueModule",
    "MessagePriority",
    "QueuedMessage",
    "EnqueueResult",
    "DequeueResult",
    "PriorityQueueConfig",
    "MetricsCollector",
    "get_metrics",
    "get_content_type",
    "MessageSigningModule",
    "SignatureResult",
    "GatewaySettings",
    "RedisSettings",
    "RateLimitSettings",
    "FeaturesSettings",
    "CircuitBreakerSettings",
    "PriorityQueueSettings",
    "MessageSigningSettings",
    "load_settings",
    "GatewayService",
    "GatewayResult",
    "AuthorizationManager",
]

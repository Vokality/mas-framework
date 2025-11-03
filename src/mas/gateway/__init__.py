"""Gateway Pattern implementation for MAS Framework."""

from .authentication import AuthenticationModule, AuthResult
from .authorization import AuthorizationModule
from .audit import AuditModule, AuditEntry
from .rate_limit import RateLimitModule, RateLimitResult
from .gateway import GatewayService, GatewayResult

__all__ = [
    "AuthenticationModule",
    "AuthResult",
    "AuthorizationModule",
    "AuditModule",
    "AuditEntry",
    "RateLimitModule",
    "RateLimitResult",
    "GatewayService",
    "GatewayResult",
]

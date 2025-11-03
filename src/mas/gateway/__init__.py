"""Gateway Pattern implementation for MAS Framework."""

from .authentication import AuthenticationModule, AuthResult
from .authorization import AuthorizationModule
from .audit import AuditModule, AuditEntry
from .rate_limit import RateLimitModule, RateLimitResult
from .dlp import DLPModule, ScanResult, Violation, ViolationType, ActionPolicy
from .gateway import GatewayService, GatewayResult

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
    "GatewayService",
    "GatewayResult",
]

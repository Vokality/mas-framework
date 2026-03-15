"""Bootstrap helpers for containerized ops-plane environments."""

from .admin import AdminBootstrapConfig, ensure_admin_user
from .client import ClientBootstrapConfig, ensure_client_enrollment

__all__ = [
    "AdminBootstrapConfig",
    "ClientBootstrapConfig",
    "ensure_admin_user",
    "ensure_client_enrollment",
]

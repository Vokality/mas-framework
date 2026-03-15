"""Bootstrap helpers for containerized ops-plane environments."""

from .admin import AdminBootstrapConfig, ensure_admin_user

__all__ = ["AdminBootstrapConfig", "ensure_admin_user"]

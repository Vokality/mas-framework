"""Authentication and authorization types for the ops plane."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class UserRole(StrEnum):
    """Supported human roles for the ops plane."""

    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """Resolved human identity from an authenticated session."""

    user_id: str
    email: str
    display_name: str
    role: UserRole
    allowed_client_ids: frozenset[str]

    def can_access_client(self, client_id: str) -> bool:
        """Return whether the user may access the target client."""

        return self.role is UserRole.ADMIN or client_id in self.allowed_client_ids


__all__ = ["AuthenticatedUser", "UserRole"]

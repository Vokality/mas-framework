"""Password hashing helpers for local ops-plane auth."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError


class PasswordService:
    """Argon2id password hashing and verification."""

    def __init__(self) -> None:
        self._hasher = PasswordHasher()

    def hash_password(self, password: str) -> str:
        """Hash a plaintext password."""

        return self._hasher.hash(password)

    def verify_password(self, password_hash: str, password: str) -> bool:
        """Verify a plaintext password against an Argon2id hash."""

        try:
            return self._hasher.verify(password_hash, password)
        except VerifyMismatchError:
            return False


__all__ = ["PasswordService"]

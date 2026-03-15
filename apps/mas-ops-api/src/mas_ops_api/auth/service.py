"""Auth and session services for the ops plane."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mas_ops_api.auth.passwords import PasswordService
from mas_ops_api.auth.types import AuthenticatedUser, UserRole
from mas_ops_api.db.base import utc_now
from mas_ops_api.db.models import (
    OpsSession,
    OpsUser,
    OpsUserClientAccess,
    OpsUserPassword,
)
from mas_ops_api.settings import OpsApiSettings


@dataclass(frozen=True, slots=True)
class LoginResult:
    """Successful login result with the issued session token."""

    token: str
    user: AuthenticatedUser


class AuthService:
    """Own local password verification and opaque session issuance."""

    def __init__(
        self,
        settings: OpsApiSettings,
        password_service: PasswordService | None = None,
    ) -> None:
        self._settings = settings
        self._password_service = password_service or PasswordService()

    @property
    def password_service(self) -> PasswordService:
        """Expose the password service for bootstrap code and tests."""

        return self._password_service

    async def login(
        self,
        session: AsyncSession,
        *,
        email: str,
        password: str,
    ) -> LoginResult | None:
        """Authenticate a user and create a new session."""

        stmt = (
            select(OpsUser, OpsUserPassword.password_hash)
            .join(OpsUserPassword, OpsUserPassword.user_id == OpsUser.user_id)
            .where(OpsUser.email == email.lower())
        )
        row = (await session.execute(stmt)).one_or_none()
        if row is None:
            return None

        user_row, password_hash = row
        if not self._password_service.verify_password(password_hash, password):
            return None

        user = await self._build_authenticated_user(session, user_row)
        token = await self._create_session(session, user.user_id)
        return LoginResult(token=token, user=user)

    async def restore_session(
        self,
        session: AsyncSession,
        *,
        token: str | None,
    ) -> AuthenticatedUser | None:
        """Resolve and refresh a session from an opaque cookie token."""

        if not token:
            return None

        stmt = select(OpsSession, OpsUser).join(
            OpsUser, OpsUser.user_id == OpsSession.user_id
        )
        stmt = stmt.where(OpsSession.session_token_hash == self._hash_token(token))
        row = (await session.execute(stmt)).one_or_none()
        if row is None:
            return None

        session_row, user_row = row
        now = utc_now()
        if session_row.revoked_at is not None:
            return None
        if session_row.idle_expires_at <= now or session_row.absolute_expires_at <= now:
            session_row.revoked_at = now
            await session.commit()
            return None

        session_row.last_seen_at = now
        session_row.idle_expires_at = now + timedelta(
            hours=self._settings.session_idle_timeout_hours
        )
        await session.commit()
        return await self._build_authenticated_user(session, user_row)

    async def logout(self, session: AsyncSession, *, token: str | None) -> None:
        """Revoke an existing session token if it exists."""

        if not token:
            return

        stmt = select(OpsSession).where(
            OpsSession.session_token_hash == self._hash_token(token)
        )
        session_row = (await session.execute(stmt)).scalar_one_or_none()
        if session_row is None or session_row.revoked_at is not None:
            return

        session_row.revoked_at = utc_now()
        await session.commit()

    async def _create_session(self, session: AsyncSession, user_id: str) -> str:
        now = utc_now()
        token = secrets.token_urlsafe(48)
        session_row = OpsSession(
            session_id=str(uuid4()),
            user_id=user_id,
            session_token_hash=self._hash_token(token),
            created_at=now,
            last_seen_at=now,
            idle_expires_at=now
            + timedelta(hours=self._settings.session_idle_timeout_hours),
            absolute_expires_at=now
            + timedelta(days=self._settings.session_absolute_timeout_days),
        )
        session.add(session_row)
        await session.commit()
        return token

    async def _build_authenticated_user(
        self,
        session: AsyncSession,
        user_row: OpsUser,
    ) -> AuthenticatedUser:
        access_stmt = select(OpsUserClientAccess.client_id).where(
            OpsUserClientAccess.user_id == user_row.user_id
        )
        allowed_client_ids = frozenset((await session.scalars(access_stmt)).all())
        return AuthenticatedUser(
            user_id=user_row.user_id,
            email=user_row.email,
            display_name=user_row.display_name,
            role=UserRole(user_row.role),
            allowed_client_ids=allowed_client_ids,
        )

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()


__all__ = ["AuthService", "LoginResult"]

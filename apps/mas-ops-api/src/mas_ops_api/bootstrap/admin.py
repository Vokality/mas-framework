"""Idempotent admin-user bootstrap for containerized ops-plane deployments."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import select

from mas_ops_api.auth.passwords import PasswordService
from mas_ops_api.auth.types import UserRole
from mas_ops_api.db.models import OpsUser, OpsUserPassword
from mas_ops_api.db.session import Database
from mas_ops_api.settings import OpsApiSettings


@dataclass(frozen=True, slots=True)
class AdminBootstrapConfig:
    """Bootstrap inputs for the initial ops-plane administrator."""

    email: str = "admin@example.com"
    password: str = "admin123"
    display_name: str = "Local Admin"


async def ensure_admin_user(
    settings: OpsApiSettings,
    *,
    config: AdminBootstrapConfig | None = None,
) -> None:
    """Ensure one admin account exists with the configured credentials."""

    resolved_config = config or AdminBootstrapConfig()
    database = Database(settings)
    password_service = PasswordService()
    try:
        async with database.session_factory() as session:
            user = (
                await session.execute(
                    select(OpsUser).where(
                        OpsUser.email == resolved_config.email.lower()
                    )
                )
            ).scalar_one_or_none()
            if user is None:
                user = OpsUser(
                    user_id=str(uuid4()),
                    email=resolved_config.email.lower(),
                    display_name=resolved_config.display_name,
                    role=UserRole.ADMIN.value,
                )
                session.add(user)
            else:
                user.email = resolved_config.email.lower()
                user.display_name = resolved_config.display_name
                user.role = UserRole.ADMIN.value

            password = await session.get(OpsUserPassword, user.user_id)
            if password is None:
                session.add(
                    OpsUserPassword(
                        user_id=user.user_id,
                        password_hash=password_service.hash_password(
                            resolved_config.password
                        ),
                    )
                )
            else:
                password.password_hash = password_service.hash_password(
                    resolved_config.password
                )
            await session.commit()
    finally:
        await database.dispose()


__all__ = ["AdminBootstrapConfig", "ensure_admin_user"]

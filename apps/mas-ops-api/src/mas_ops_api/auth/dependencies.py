"""FastAPI auth dependencies for the ops plane."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from mas_ops_api.auth.service import AuthService
from mas_ops_api.auth.types import AuthenticatedUser, UserRole
from mas_ops_api.services import OpsApiServices
from mas_ops_api.settings import OpsApiSettings


def get_services(request: Request) -> OpsApiServices:
    """Return the shared app services container."""

    return request.app.state.services


async def get_db_session(
    services: OpsApiServices = Depends(get_services),
) -> AsyncIterator[AsyncSession]:
    """Yield an async database session."""

    async with services.database.session_factory() as session:
        yield session


def get_auth_service(services: OpsApiServices = Depends(get_services)) -> AuthService:
    """Return the auth service singleton."""

    return services.auth_service


def get_settings(services: OpsApiServices = Depends(get_services)) -> OpsApiSettings:
    """Return the resolved application settings."""

    return services.settings


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    auth_service: AuthService = Depends(get_auth_service),
    services: OpsApiServices = Depends(get_services),
) -> AuthenticatedUser:
    """Resolve the current human user from the session cookie."""

    token = request.cookies.get(services.settings.session_cookie_name)
    user = await auth_service.restore_session(session, token=token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthenticated",
        )
    return user


def require_roles(
    *roles: UserRole,
) -> Callable[[AuthenticatedUser], Awaitable[AuthenticatedUser]]:
    """Build a dependency that enforces one of the allowed user roles."""

    async def dependency(
        user: AuthenticatedUser = Depends(get_current_user),
    ) -> AuthenticatedUser:
        if user.role not in set(roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="forbidden",
            )
        return user

    return dependency


__all__ = [
    "get_auth_service",
    "get_current_user",
    "get_db_session",
    "get_services",
    "get_settings",
    "require_roles",
]

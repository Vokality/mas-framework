"""Authentication routes for the ops-plane API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from mas_ops_api.api.schemas import LoginRequest, SessionResponse
from mas_ops_api.auth.dependencies import (
    get_auth_service,
    get_current_user,
    get_db_session,
    get_settings,
)
from mas_ops_api.auth.service import AuthService
from mas_ops_api.auth.types import AuthenticatedUser
from mas_ops_api.settings import OpsApiSettings


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=SessionResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    auth_service: AuthService = Depends(get_auth_service),
    settings: OpsApiSettings = Depends(get_settings),
) -> SessionResponse:
    """Authenticate a human user and issue a server-side session."""

    result = await auth_service.login(
        session,
        email=payload.email,
        password=payload.password,
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_credentials",
        )

    response.set_cookie(
        settings.session_cookie_name,
        result.token,
        httponly=True,
        samesite="lax",
        secure=settings.use_secure_cookies,
        max_age=settings.session_absolute_timeout_days * 24 * 60 * 60,
    )
    return SessionResponse.from_authenticated_user(result.user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    auth_service: AuthService = Depends(get_auth_service),
    settings: OpsApiSettings = Depends(get_settings),
) -> Response:
    """Destroy the current session if it exists."""

    token = request.cookies.get(settings.session_cookie_name)
    await auth_service.logout(session, token=token)
    logout_response = Response(status_code=status.HTTP_204_NO_CONTENT)
    logout_response.delete_cookie(settings.session_cookie_name)
    return logout_response


@router.get("/session", response_model=SessionResponse)
async def restore_session(
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> SessionResponse:
    """Restore the current human session."""

    return SessionResponse.from_authenticated_user(current_user)


__all__ = ["router"]

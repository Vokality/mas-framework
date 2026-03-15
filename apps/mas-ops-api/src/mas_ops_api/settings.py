"""Settings for the MSP ops-plane API."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class OpsApiSettings(BaseSettings):
    """Runtime settings for the FastAPI ops plane."""

    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/mas_ops",
        description="SQLAlchemy database URL for the ops-plane datastore.",
    )
    auto_create_schema: bool = Field(
        default=False,
        description="Create database tables on startup for local development and tests.",
    )
    environment: Literal["development", "test", "production"] = Field(
        default="development",
        description="Deployment environment for cookie and runtime defaults.",
    )
    public_base_url: str = Field(
        default="http://localhost:8080",
        description="Public API origin used to derive secure-cookie defaults.",
    )
    allowed_origins: list[str] = Field(
        default_factory=list,
        description="Explicit browser origins allowed to call the API with credentials.",
    )
    session_cookie_name: str = Field(
        default="mas_ops_session",
        description="Opaque session cookie name.",
    )
    session_idle_timeout_hours: int = Field(
        default=12,
        ge=1,
        description="Idle timeout for human sessions.",
    )
    session_absolute_timeout_days: int = Field(
        default=7,
        ge=1,
        description="Absolute maximum age for human sessions.",
    )
    sse_retry_ms: int = Field(
        default=2_000,
        ge=100,
        description="Retry hint for SSE clients.",
    )
    approval_expiry_poll_seconds: float = Field(
        default=5.0,
        ge=0.0,
        description="Polling interval for expiring pending approvals; set to 0 to disable.",
    )

    model_config = SettingsConfigDict(
        env_prefix="MAS_OPS_API_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def use_secure_cookies(self) -> bool:
        """Return whether the session cookie should set the Secure flag."""

        host = urlparse(self.public_base_url).hostname or ""
        if host in {"localhost", "127.0.0.1"}:
            return False
        return True

    @property
    def cors_allowed_origins(self) -> list[str]:
        """Return browser origins allowed to call the API."""

        if self.allowed_origins:
            return self.allowed_origins
        if self.environment in {"development", "test"}:
            return [
                "http://localhost:4173",
                "http://127.0.0.1:4173",
            ]
        return []


__all__ = ["OpsApiSettings"]

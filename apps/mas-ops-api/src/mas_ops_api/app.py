"""FastAPI application factory for the MSP ops-plane API."""

from __future__ import annotations

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware import Middleware
from starlette.types import ASGIApp

from mas_ops_api.api import api_router
from mas_ops_api.auth.passwords import PasswordService
from mas_ops_api.auth.service import AuthService
from mas_ops_api.chat.service import ChatService
from mas_ops_api.config.service import ConfigService
from mas_ops_api.connectors import (
    ConnectorRegistry,
    OpsPlaneFabricConnector,
    PortfolioIngressRegistry,
)
from mas_ops_api.db.bootstrap import create_schema
from mas_ops_api.db.session import Database
from mas_ops_api.projections import PortfolioIngestService
from mas_ops_api.services import OpsApiServices
from mas_ops_api.settings import OpsApiSettings
from mas_ops_api.streams.service import StreamService


def build_cors_middleware(
    app: ASGIApp,
    /,
    *,
    allow_origins: list[str],
    allow_credentials: bool,
    allow_methods: list[str],
    allow_headers: list[str],
) -> ASGIApp:
    """Adapt Starlette's CORS middleware to a fully typed middleware factory."""

    return CORSMiddleware(
        app,
        allow_origins=allow_origins,
        allow_credentials=allow_credentials,
        allow_methods=allow_methods,
        allow_headers=allow_headers,
    )


def create_app(settings: OpsApiSettings | None = None) -> FastAPI:
    """Create the FastAPI application."""

    app_settings = settings or OpsApiSettings()
    database = Database(app_settings)
    password_service = PasswordService()
    stream_service = StreamService(app_settings)
    portfolio_ingest_service = PortfolioIngestService(database, stream_service)
    middleware: list[Middleware] = []
    if app_settings.cors_allowed_origins:
        middleware.append(
            Middleware(
                build_cors_middleware,  # type: ignore[invalid-argument-type] ty does not recognize Starlette middleware factories that return ASGIApp.
                allow_origins=app_settings.cors_allowed_origins,
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )
        )
    services = OpsApiServices(
        settings=app_settings,
        database=database,
        auth_service=AuthService(app_settings, password_service=password_service),
        chat_service=ChatService(stream_service),
        config_service=ConfigService(stream_service),
        command_connector_registry=ConnectorRegistry(),
        portfolio_ingress_registry=PortfolioIngressRegistry(
            factory=lambda client_id: OpsPlaneFabricConnector(
                client_id=client_id,
                portfolio_ingest_service=portfolio_ingest_service,
            )
        ),
        portfolio_ingest_service=portfolio_ingest_service,
        stream_service=stream_service,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.services = services
        if app_settings.auto_create_schema:
            await create_schema(database.engine)
        yield
        await database.dispose()

    app = FastAPI(
        title="MAS Ops API",
        version="0.4.5",
        lifespan=lifespan,
        middleware=middleware,
    )
    app.include_router(api_router)
    return app


__all__ = ["OpsApiServices", "create_app"]

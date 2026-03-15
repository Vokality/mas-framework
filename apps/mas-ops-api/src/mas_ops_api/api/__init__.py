"""FastAPI routers and response models for the ops plane."""

from fastapi import APIRouter

from .routes.approvals import router as approvals_router
from .routes.assets import router as assets_router
from .routes.auth import router as auth_router
from .routes.chat import router as chat_router
from .routes.clients import router as clients_router
from .routes.config import router as config_router
from .routes.health import router as health_router
from .routes.incidents import router as incidents_router
from .routes.streams import router as streams_router


api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(clients_router)
api_router.include_router(incidents_router)
api_router.include_router(assets_router)
api_router.include_router(chat_router)
api_router.include_router(approvals_router)
api_router.include_router(config_router)
api_router.include_router(streams_router)

__all__ = ["api_router"]

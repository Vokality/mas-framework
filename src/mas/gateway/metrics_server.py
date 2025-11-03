"""Metrics HTTP Server - Exposes Prometheus metrics endpoint.

Provides a simple HTTP server for Prometheus to scrape metrics from the gateway.
"""

import logging
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Route

from .metrics import get_metrics, get_content_type

logger = logging.getLogger(__name__)


async def metrics_endpoint(request):
    """
    Prometheus metrics endpoint.

    Returns:
        Response with Prometheus-formatted metrics
    """
    metrics_data = get_metrics()
    return Response(
        content=metrics_data,
        media_type=get_content_type(),
    )


async def health_endpoint(request):
    """
    Health check endpoint.

    Returns:
        200 OK if service is running
    """
    return Response(
        content=b'{"status": "ok"}',
        media_type="application/json",
    )


# Create Starlette app
app = Starlette(
    debug=False,
    routes=[
        Route("/metrics", endpoint=metrics_endpoint, methods=["GET"]),
        Route("/health", endpoint=health_endpoint, methods=["GET"]),
    ],
)


def create_metrics_app():
    """
    Create metrics HTTP application.

    Returns:
        Starlette application with /metrics and /health endpoints
    """
    return app

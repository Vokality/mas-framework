"""SSE helpers for the ops-plane API."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import Request
from starlette.responses import StreamingResponse

from mas_ops_api.services import OpsApiServices
from mas_ops_api.streams.service import StreamScope


def build_sse_response(
    request: Request,
    *,
    services: OpsApiServices,
    scope: StreamScope,
    last_event_id: str | None,
    replay_only: bool = False,
) -> StreamingResponse:
    """Create an SSE response with replay and live in-process updates."""

    async def iterator() -> AsyncIterator[str]:
        async with services.database.session_factory() as session:
            replay = await services.stream_service.load_replay(
                session,
                scope=scope,
                last_event_id=last_event_id,
            )
        for record in replay:
            yield services.stream_service.encode_sse(record)
        if replay_only:
            return

        subscriber_id, queue = await services.stream_service.broker.subscribe()
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    record = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue

                if services.stream_service.matches_scope(record, scope):
                    yield services.stream_service.encode_sse(record)
        finally:
            await services.stream_service.broker.unsubscribe(subscriber_id)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        iterator(), media_type="text/event-stream", headers=headers
    )


__all__ = ["build_sse_response"]

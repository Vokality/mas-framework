"""SSE route tests for the Phase 1 ops-plane API."""

from __future__ import annotations

import json

import pytest

from mas_ops_api.auth.types import UserRole

from .conftest import CLIENT_A, CLIENT_B, INCIDENT_A


async def _read_sse_events(response, *, expected_count: int) -> list[dict[str, object]]:  # noqa: ANN001
    events: list[dict[str, object]] = []
    current: dict[str, str] = {}
    async for line in response.aiter_lines():
        if line == "":
            if current:
                events.append(dict(current))
                current.clear()
                if len(events) >= expected_count:
                    break
            continue
        key, _, value = line.partition(":")
        current[key] = value.lstrip()
    return events


@pytest.mark.asyncio
async def test_portfolio_stream_replays_authorized_events_and_last_event_id(
    api_client,
    seed_user,
    seed_portfolio,
    insert_stream_events,
    login,
) -> None:
    await seed_portfolio()
    await seed_user(
        email="operator@example.com",
        password="password-1",
        role=UserRole.OPERATOR,
        client_ids=(CLIENT_A,),
    )
    await insert_stream_events(
        [
            {
                "event_name": "client.updated",
                "client_id": CLIENT_A,
                "incident_id": None,
                "chat_session_id": None,
                "subject_type": "client",
                "subject_id": CLIENT_A,
                "payload": {"client_id": CLIENT_A, "name": "Acme Corp"},
            },
            {
                "event_name": "client.updated",
                "client_id": CLIENT_B,
                "incident_id": None,
                "chat_session_id": None,
                "subject_type": "client",
                "subject_id": CLIENT_B,
                "payload": {"client_id": CLIENT_B, "name": "Beta Corp"},
            },
            {
                "event_name": "portfolio.updated",
                "client_id": CLIENT_A,
                "incident_id": None,
                "chat_session_id": None,
                "subject_type": "portfolio",
                "subject_id": "portfolio",
                "payload": {"summary": "Portfolio recalculated"},
            },
        ]
    )
    await login("operator@example.com", "password-1")

    async with api_client.stream(
        "GET", "/streams/portfolio?replay_only=true"
    ) as response:
        assert response.status_code == 200
        events = await _read_sse_events(response, expected_count=2)

    assert [event["event"] for event in events] == [
        "client.updated",
        "portfolio.updated",
    ]
    first_stream_id = events[0]["id"]

    async with api_client.stream(
        "GET",
        "/streams/portfolio?replay_only=true",
        headers={"Last-Event-ID": first_stream_id},
    ) as response:
        assert response.status_code == 200
        replay = await _read_sse_events(response, expected_count=1)

    assert len(replay) == 1
    assert replay[0]["event"] == "portfolio.updated"
    replay_payload = json.loads(replay[0]["data"])
    assert replay_payload["client_id"] == CLIENT_A


@pytest.mark.asyncio
async def test_client_and_incident_streams_enforce_scope(
    api_client,
    seed_user,
    seed_portfolio,
    insert_stream_events,
    login,
) -> None:
    await seed_portfolio()
    await seed_user(
        email="operator@example.com",
        password="password-1",
        role=UserRole.OPERATOR,
        client_ids=(CLIENT_A,),
    )
    await login("operator@example.com", "password-1")

    forbidden_client = await api_client.get(f"/streams/clients/{CLIENT_B}")
    assert forbidden_client.status_code == 403

    forbidden_incident = await api_client.get(
        "/streams/incidents/66666666-6666-4666-8666-666666666666"
    )
    assert forbidden_incident.status_code == 403

    await insert_stream_events(
        [
            {
                "event_name": "incident.updated",
                "client_id": CLIENT_A,
                "incident_id": INCIDENT_A,
                "chat_session_id": None,
                "subject_type": "incident",
                "subject_id": INCIDENT_A,
                "payload": {"incident_id": INCIDENT_A, "summary": "Updated"},
            }
        ]
    )
    async with api_client.stream(
        "GET",
        f"/streams/incidents/{INCIDENT_A}?replay_only=true",
    ) as response:
        assert response.status_code == 200
        events = await _read_sse_events(response, expected_count=1)
    assert events[0]["event"] == "incident.updated"

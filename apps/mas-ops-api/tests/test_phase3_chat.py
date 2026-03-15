"""Phase 3 chat, incident cockpit, and diagnostics integration tests."""

from __future__ import annotations

import asyncio

import pytest

from mas_ops_api.auth.types import UserRole

from .conftest import CLIENT_A, INCIDENT_A


async def _wait_for_turn_completion(
    api_client, chat_session_id: str
) -> dict[str, object]:  # noqa: ANN001
    for _ in range(20):
        response = await api_client.get(f"/chat/sessions/{chat_session_id}")
        assert response.status_code == 200
        session = response.json()
        if session["turns"] and session["turns"][-1]["state"] == "completed":
            return session
        await asyncio.sleep(0.01)
    raise AssertionError("chat turn did not complete in time")


@pytest.mark.asyncio
async def test_global_chat_generates_assistant_reply(
    api_client,
    seed_user,
    seed_portfolio,
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

    create_response = await api_client.post("/chat/sessions", json={"scope": "global"})
    assert create_response.status_code == 201
    chat_session_id = create_response.json()["chat_session_id"]

    append_response = await api_client.post(
        f"/chat/sessions/{chat_session_id}/messages",
        json={"message": "Summarize the current portfolio risks."},
    )
    assert append_response.status_code == 201
    assert append_response.json()["state"] == "running"

    hydrated = await _wait_for_turn_completion(api_client, chat_session_id)

    assert hydrated["messages"][-1]["role"] == "assistant"
    assert "portfolio" in hydrated["messages"][-1]["content"].lower()


@pytest.mark.asyncio
async def test_incident_chat_collects_evidence_and_updates_incident_detail(
    api_client,
    seed_user,
    seed_portfolio,
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

    create_response = await api_client.post(
        "/chat/sessions",
        json={
            "scope": "incident",
            "client_id": CLIENT_A,
            "fabric_id": "33333333-3333-4333-8333-333333333333",
            "incident_id": INCIDENT_A,
        },
    )
    assert create_response.status_code == 201
    chat_session_id = create_response.json()["chat_session_id"]

    append_response = await api_client.post(
        f"/chat/sessions/{chat_session_id}/messages",
        json={"message": "Collect more evidence about the unstable uplink."},
    )
    assert append_response.status_code == 201
    assert append_response.json()["state"] == "running"

    hydrated = await _wait_for_turn_completion(api_client, chat_session_id)
    assert hydrated["messages"][-1]["role"] == "assistant"

    incident_response = await api_client.get(f"/incidents/{INCIDENT_A}")
    assert incident_response.status_code == 200
    incident = incident_response.json()
    assert incident["assets"]
    assert incident["evidence_bundles"]
    assert incident["recommended_actions"]
    assert incident["state"] == "investigating"

    activity_response = await api_client.get(f"/incidents/{INCIDENT_A}/activity")
    assert activity_response.status_code == 200
    assert any(
        item["event_type"] == "diagnostics.completed"
        for item in activity_response.json()
    )

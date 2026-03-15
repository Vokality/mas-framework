"""Backend tests for the Phase 1 ops-plane API."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from mas_msp_contracts import ApprovalState
from mas_ops_api.auth.types import UserRole
from mas_ops_api.db.models import OpsUser, OpsUserClientAccess

from .conftest import (
    APPROVAL_A,
    ASSET_A,
    ASSET_B,
    CLIENT_A,
    CLIENT_B,
    INCIDENT_A,
    expire_current_session,
)


@pytest.mark.asyncio
async def test_auth_login_restore_logout(
    api_client, seed_user, login, session_cookie_name
) -> None:
    await seed_user(
        email="viewer@example.com",
        password="password-1",
        role=UserRole.VIEWER,
        client_ids=(CLIENT_A,),
    )

    login_response = await login("viewer@example.com", "password-1")
    assert login_response.status_code == 200
    assert login_response.cookies.get(session_cookie_name)
    assert login_response.json()["role"] == "viewer"
    assert login_response.json()["client_ids"] == [CLIENT_A]

    session_response = await api_client.get("/auth/session")
    assert session_response.status_code == 200
    assert session_response.json()["email"] == "viewer@example.com"

    logout_response = await api_client.post("/auth/logout")
    assert logout_response.status_code == 204

    expired_response = await api_client.get("/auth/session")
    assert expired_response.status_code == 401


@pytest.mark.asyncio
async def test_auth_rejects_invalid_credentials(api_client, seed_user, login) -> None:
    await seed_user(
        email="viewer@example.com",
        password="password-1",
        role=UserRole.VIEWER,
        client_ids=(CLIENT_A,),
    )

    response = await login("viewer@example.com", "wrong-password")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_auth_idle_and_absolute_timeouts(
    api_client,
    seed_user,
    login,
    session_factory,
) -> None:
    await seed_user(
        email="operator@example.com",
        password="password-1",
        role=UserRole.OPERATOR,
        client_ids=(CLIENT_A,),
    )
    response = await login("operator@example.com", "password-1")
    assert response.status_code == 200

    await expire_current_session(session_factory, idle_delta=timedelta(minutes=1))
    idle_response = await api_client.get("/auth/session")
    assert idle_response.status_code == 401

    response = await login("operator@example.com", "password-1")
    assert response.status_code == 200
    await expire_current_session(session_factory, absolute_delta=timedelta(minutes=1))
    absolute_response = await api_client.get("/auth/session")
    assert absolute_response.status_code == 401


@pytest.mark.asyncio
async def test_client_scoped_read_routes(
    api_client, seed_user, seed_portfolio, login
) -> None:
    await seed_portfolio()
    await seed_user(
        email="operator@example.com",
        password="password-1",
        role=UserRole.OPERATOR,
        client_ids=(CLIENT_A,),
    )
    await login("operator@example.com", "password-1")

    clients_response = await api_client.get("/clients")
    assert clients_response.status_code == 200
    assert [item["client_id"] for item in clients_response.json()] == [CLIENT_A]

    assert (await api_client.get(f"/clients/{CLIENT_A}")).status_code == 200
    assert (await api_client.get(f"/clients/{CLIENT_B}")).status_code == 403

    incidents_response = await api_client.get(f"/clients/{CLIENT_A}/incidents")
    assert incidents_response.status_code == 200
    assert incidents_response.json()[0]["incident_id"] == INCIDENT_A

    activity_response = await api_client.get(f"/incidents/{INCIDENT_A}/activity")
    assert activity_response.status_code == 200
    assert activity_response.json()[0]["subject_id"] == INCIDENT_A

    assets_response = await api_client.get(f"/clients/{CLIENT_A}/assets")
    assert assets_response.status_code == 200
    assert assets_response.json()[0]["asset_id"] == ASSET_A

    assert (await api_client.get(f"/assets/{ASSET_A}")).status_code == 200
    assert (await api_client.get(f"/assets/{ASSET_B}")).status_code == 403


@pytest.mark.asyncio
async def test_chat_routes_persist_sessions_and_messages(
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
    chat_session = create_response.json()
    assert chat_session["scope"] == "incident"
    assert chat_session["turns"] == []
    assert chat_session["messages"] == []

    append_response = await api_client.post(
        f"/chat/sessions/{chat_session['chat_session_id']}/messages",
        json={"message": "Collect more evidence."},
    )
    assert append_response.status_code == 201
    assert append_response.json()["state"] == "running"

    get_response = await api_client.get(
        f"/chat/sessions/{chat_session['chat_session_id']}"
    )
    assert get_response.status_code == 200
    hydrated = get_response.json()
    assert len(hydrated["turns"]) == 1
    assert len(hydrated["messages"]) == 1
    assert hydrated["messages"][0]["content"] == "Collect more evidence."


@pytest.mark.asyncio
async def test_approval_and_config_routes_enforce_roles(
    api_client,
    seed_user,
    seed_portfolio,
    login,
) -> None:
    await seed_portfolio()
    await seed_user(
        email="viewer@example.com",
        password="password-1",
        role=UserRole.VIEWER,
        client_ids=(CLIENT_A,),
    )
    await seed_user(
        email="operator@example.com",
        password="password-1",
        role=UserRole.OPERATOR,
        client_ids=(CLIENT_A,),
    )
    await seed_user(
        email="admin@example.com",
        password="password-1",
        role=UserRole.ADMIN,
    )

    await login("viewer@example.com", "password-1")
    assert (
        await api_client.post(
            f"/approvals/{APPROVAL_A}/decision",
            json={"decision": "approve", "reason": "approved"},
        )
    ).status_code == 403
    assert (
        await api_client.post(
            f"/clients/{CLIENT_A}/config/runs/run-1/cancel",
            json={"reason": "nope"},
        )
    ).status_code == 403
    assert (
        await api_client.put(
            f"/clients/{CLIENT_A}/config/desired-state",
            json={
                "client_id": CLIENT_A,
                "fabric_id": "33333333-3333-4333-8333-333333333333",
                "desired_state_version": 2,
                "tenant_metadata": {},
                "policy": {},
                "inventory_sources": [],
                "notification_routes": [],
            },
        )
    ).status_code == 403
    await api_client.post("/auth/logout")

    await login("operator@example.com", "password-1")
    approval_response = await api_client.post(
        f"/approvals/{APPROVAL_A}/decision",
        json={"decision": "approve", "reason": "approved"},
    )
    assert approval_response.status_code == 200
    assert approval_response.json()["state"] == ApprovalState.APPROVED.value
    assert (
        await api_client.post(
            f"/clients/{CLIENT_A}/config/runs/run-1/cancel",
            json={"reason": "nope"},
        )
    ).status_code == 403
    assert (
        await api_client.post(f"/clients/{CLIENT_A}/config/validate")
    ).status_code == 403
    await api_client.post("/auth/logout")

    await login("admin@example.com", "password-1")
    desired_state_response = await api_client.get(
        f"/clients/{CLIENT_A}/config/desired-state"
    )
    assert desired_state_response.status_code == 200
    assert desired_state_response.json()["desired_state_version"] == 1

    replace_response = await api_client.put(
        f"/clients/{CLIENT_A}/config/desired-state",
        json={
            "client_id": CLIENT_A,
            "fabric_id": "33333333-3333-4333-8333-333333333333",
            "desired_state_version": 2,
            "tenant_metadata": {"display_name": "Acme Corp"},
            "policy": {"default_mode": "deny"},
            "inventory_sources": [{"kind": "snmp"}],
            "notification_routes": [{"kind": "email"}],
        },
    )
    assert replace_response.status_code == 200
    assert replace_response.json()["desired_state_version"] == 2

    stale_replace = await api_client.put(
        f"/clients/{CLIENT_A}/config/desired-state",
        json={
            "client_id": CLIENT_A,
            "fabric_id": "33333333-3333-4333-8333-333333333333",
            "desired_state_version": 2,
            "tenant_metadata": {},
            "policy": {},
            "inventory_sources": [],
            "notification_routes": [],
        },
    )
    assert stale_replace.status_code == 409

    validation_response = await api_client.post(f"/clients/{CLIENT_A}/config/validate")
    assert validation_response.status_code == 200
    assert validation_response.json()["status"] == "valid"

    apply_response = await api_client.post(f"/clients/{CLIENT_A}/config/apply")
    assert apply_response.status_code == 200
    assert apply_response.json()["status"] == "pending"


@pytest.mark.asyncio
async def test_database_uniqueness_constraints(session_factory, seed_user) -> None:
    await seed_user(
        email="duplicate@example.com",
        password="password-1",
        role=UserRole.VIEWER,
        client_ids=(CLIENT_A,),
    )

    with pytest.raises(IntegrityError):
        await seed_user(
            email="duplicate@example.com",
            password="password-2",
            role=UserRole.OPERATOR,
            client_ids=(CLIENT_A,),
        )

    async with session_factory() as session:
        user = OpsUser(
            user_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            email="fresh@example.com",
            display_name="Fresh User",
            role=UserRole.VIEWER.value,
        )
        session.add(user)
        session.add(OpsUserClientAccess(user_id=user.user_id, client_id=CLIENT_A))
        session.add(OpsUserClientAccess(user_id=user.user_id, client_id=CLIENT_A))
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_admin_can_list_all_clients(
    api_client, seed_user, seed_portfolio, login
) -> None:
    await seed_portfolio()
    await seed_user(
        email="admin@example.com",
        password="password-1",
        role=UserRole.ADMIN,
    )
    await login("admin@example.com", "password-1")

    response = await api_client.get("/clients")
    assert response.status_code == 200
    assert {item["client_id"] for item in response.json()} == {CLIENT_A, CLIENT_B}


@pytest.mark.asyncio
async def test_local_ui_origin_is_allowed_for_credentialed_cors(api_client) -> None:
    response = await api_client.options(
        "/auth/session",
        headers={
            "origin": "http://localhost:4173",
            "access-control-request-method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:4173"
    assert response.headers["access-control-allow-credentials"] == "true"

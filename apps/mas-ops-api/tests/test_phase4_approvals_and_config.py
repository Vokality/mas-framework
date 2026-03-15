"""Phase 4 approval, resume, config, and audit integration tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from mas_msp_contracts import ApprovalState, ConfigApplyState
from mas_ops_api.auth.types import UserRole
from mas_ops_api.db.models import ApprovalRequestRecord, OpsAuditEntry, OpsStreamEvent

from .conftest import CLIENT_A, INCIDENT_A


async def _wait_for_turn_state(
    api_client,
    chat_session_id: str,
    expected_state: str,
) -> dict[str, object]:  # noqa: ANN001
    for _ in range(40):
        response = await api_client.get(f"/chat/sessions/{chat_session_id}")
        assert response.status_code == 200
        session = response.json()
        if session["turns"] and session["turns"][-1]["state"] == expected_state:
            return session
        await asyncio.sleep(0.01)
    raise AssertionError(f"chat turn did not reach {expected_state!r} in time")


@pytest.mark.asyncio
async def test_incident_write_actions_wait_for_approval_and_resume_after_approval(
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
        json={"message": "Bounce the uplink to restore service."},
    )
    assert append_response.status_code == 201

    waiting_session = await _wait_for_turn_state(
        api_client,
        chat_session_id,
        "waiting_for_approval",
    )
    approval_id = waiting_session["turns"][-1]["approval_id"]
    assert approval_id is not None

    incident_response = await api_client.get(f"/incidents/{INCIDENT_A}")
    assert incident_response.status_code == 200
    incident = incident_response.json()
    assert incident["state"] == "awaiting_approval"
    assert any(item["approval_id"] == approval_id for item in incident["approvals"])

    approvals_response = await api_client.get("/approvals")
    assert approvals_response.status_code == 200
    assert any(
        item["approval_id"] == approval_id and item["state"] == ApprovalState.PENDING
        for item in approvals_response.json()
    )

    decision_response = await api_client.post(
        f"/approvals/{approval_id}/decision",
        json={"decision": "approve", "reason": "Proceed"},
    )
    assert decision_response.status_code == 200
    assert decision_response.json()["state"] == ApprovalState.EXECUTED

    completed_session = await _wait_for_turn_state(
        api_client, chat_session_id, "completed"
    )
    assert "approved" in completed_session["messages"][-1]["content"].lower()

    incident_response = await api_client.get(f"/incidents/{INCIDENT_A}")
    incident = incident_response.json()
    assert incident["state"] == "remediating"
    assert any(
        entry["event_type"] == "remediation.executed" for entry in incident["activity"]
    )


@pytest.mark.asyncio
async def test_config_apply_requires_approval_and_rejection_cancels_run(
    api_client,
    seed_user,
    seed_portfolio,
    login,
) -> None:
    await seed_portfolio()
    await seed_user(
        email="admin@example.com",
        password="password-1",
        role=UserRole.ADMIN,
    )
    await login("admin@example.com", "password-1")

    apply_response = await api_client.post(f"/clients/{CLIENT_A}/config/apply")
    assert apply_response.status_code == 200
    assert apply_response.json()["status"] == ConfigApplyState.PENDING

    approvals_response = await api_client.get("/approvals")
    approval = next(
        item
        for item in approvals_response.json()
        if item["client_id"] == CLIENT_A and item["action_kind"] == "config.apply"
    )
    assert approval["state"] == ApprovalState.PENDING

    decision_response = await api_client.post(
        f"/approvals/{approval['approval_id']}/decision",
        json={"decision": "reject", "reason": "Do not apply yet"},
    )
    assert decision_response.status_code == 200
    assert decision_response.json()["state"] == ApprovalState.REJECTED

    runs_response = await api_client.get(f"/clients/{CLIENT_A}/config/runs")
    assert runs_response.status_code == 200
    assert runs_response.json()["apply_runs"][0]["status"] == ConfigApplyState.CANCELLED


@pytest.mark.asyncio
async def test_config_apply_run_cancel_cancels_pending_approval(
    api_client,
    seed_user,
    seed_portfolio,
    login,
) -> None:
    await seed_portfolio()
    await seed_user(
        email="admin@example.com",
        password="password-1",
        role=UserRole.ADMIN,
    )
    await login("admin@example.com", "password-1")

    apply_response = await api_client.post(f"/clients/{CLIENT_A}/config/apply")
    assert apply_response.status_code == 200
    run_id = apply_response.json()["config_apply_run_id"]

    cancel_response = await api_client.post(
        f"/clients/{CLIENT_A}/config/runs/{run_id}/cancel",
        json={"reason": "Withdraw the pending apply"},
    )
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == ConfigApplyState.CANCELLED

    approvals_response = await api_client.get("/approvals")
    approval = next(
        item
        for item in approvals_response.json()
        if item["client_id"] == CLIENT_A and item["action_kind"] == "config.apply"
    )
    assert approval["state"] == ApprovalState.CANCELLED


@pytest.mark.asyncio
async def test_pending_remediation_approvals_expire_and_unwind_waiting_turn(
    api_client,
    ops_app,
    seed_user,
    seed_portfolio,
    login,
    session_factory,
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
        json={"message": "Bounce the uplink to restore service."},
    )
    assert append_response.status_code == 201

    waiting_session = await _wait_for_turn_state(
        api_client,
        chat_session_id,
        "waiting_for_approval",
    )
    approval_id = waiting_session["turns"][-1]["approval_id"]
    assert approval_id is not None

    async with session_factory() as session:
        approval = await session.get(ApprovalRequestRecord, approval_id)
        assert approval is not None
        approval.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()

    expired = await ops_app.state.services.approval_service.expire_pending(
        now=datetime.now(UTC)
    )
    assert [item.approval_id for item in expired] == [approval_id]

    cancelled_session = await _wait_for_turn_state(
        api_client, chat_session_id, "cancelled"
    )
    assert "expired" in cancelled_session["messages"][-1]["content"].lower()

    incident_response = await api_client.get(f"/incidents/{INCIDENT_A}")
    assert incident_response.status_code == 200
    incident = incident_response.json()
    assert incident["state"] == "investigating"
    assert any(
        entry["event_type"] == "remediation.expired" for entry in incident["activity"]
    )

    approvals_response = await api_client.get("/approvals")
    approval = next(
        item for item in approvals_response.json() if item["approval_id"] == approval_id
    )
    assert approval["state"] == ApprovalState.EXPIRED


@pytest.mark.asyncio
async def test_expiry_sweep_is_idempotent_and_emits_terminal_side_effects_once(
    ops_app,
    session_factory,
    seed_portfolio,
) -> None:
    await seed_portfolio()
    now = datetime.now(UTC)
    async with session_factory() as session:
        approval = await session.get(
            ApprovalRequestRecord, "99999999-9999-4999-8999-999999999999"
        )
        assert approval is not None
        approval.expires_at = now - timedelta(minutes=1)
        await session.commit()

    first = await ops_app.state.services.approval_service.expire_pending(now=now)
    second = await ops_app.state.services.approval_service.expire_pending(now=now)

    assert [item.approval_id for item in first] == [
        "99999999-9999-4999-8999-999999999999"
    ]
    assert second == []

    async with session_factory() as session:
        audit_entries = list(
            (
                await session.scalars(
                    select(OpsAuditEntry).where(
                        OpsAuditEntry.approval_id
                        == "99999999-9999-4999-8999-999999999999",
                        OpsAuditEntry.action == "approval.expired",
                    )
                )
            ).all()
        )
        stream_entries = list(
            (
                await session.scalars(
                    select(OpsStreamEvent).where(
                        OpsStreamEvent.subject_id
                        == "99999999-9999-4999-8999-999999999999",
                        OpsStreamEvent.event_name == "approval.expired",
                    )
                )
            ).all()
        )

    assert len(audit_entries) == 1
    assert len(stream_entries) == 1


@pytest.mark.asyncio
async def test_config_validation_and_apply_emit_audit_entries(
    api_client,
    seed_user,
    seed_portfolio,
    login,
    session_factory,
) -> None:
    await seed_portfolio()
    await seed_user(
        email="admin@example.com",
        password="password-1",
        role=UserRole.ADMIN,
    )
    await login("admin@example.com", "password-1")

    replace_response = await api_client.put(
        f"/clients/{CLIENT_A}/config/desired-state",
        json={
            "client_id": CLIENT_A,
            "fabric_id": "33333333-3333-4333-8333-333333333333",
            "desired_state_version": 2,
            "tenant_metadata": {"display_name": "Acme Corp"},
            "policy": {"default_mode": "deny"},
            "inventory_sources": [{"kind": "snmp"}],
            "notification_routes": [{"kind": "email", "target": "noc@example.com"}],
        },
    )
    assert replace_response.status_code == 200

    validation_response = await api_client.post(f"/clients/{CLIENT_A}/config/validate")
    assert validation_response.status_code == 200
    assert validation_response.json()["status"] == "valid"

    apply_response = await api_client.post(f"/clients/{CLIENT_A}/config/apply")
    assert apply_response.status_code == 200

    approvals_response = await api_client.get("/approvals")
    approval = next(
        item
        for item in approvals_response.json()
        if item["client_id"] == CLIENT_A and item["action_kind"] == "config.apply"
    )
    await api_client.post(
        f"/approvals/{approval['approval_id']}/decision",
        json={"decision": "approve", "reason": "Ship it"},
    )

    runs_response = await api_client.get(f"/clients/{CLIENT_A}/config/runs")
    assert runs_response.status_code == 200
    assert runs_response.json()["apply_runs"][0]["status"] == ConfigApplyState.SUCCEEDED

    async with session_factory() as session:
        entries = list(
            (
                await session.scalars(
                    select(OpsAuditEntry).where(OpsAuditEntry.client_id == CLIENT_A)
                )
            ).all()
        )

    actions = {entry.action for entry in entries}
    assert "config.desired_state.replaced" in actions
    assert "config.validation.completed" in actions
    assert "approval.requested" in actions
    assert "approval.executed" in actions
    assert "config.apply.completed" in actions

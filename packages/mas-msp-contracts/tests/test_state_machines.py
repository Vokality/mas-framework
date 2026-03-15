"""State machine tests for MSP Phase 0 foundations."""

from __future__ import annotations

import pytest

from mas_msp_contracts import (
    APPROVAL_STATE_MACHINE,
    CHAT_TURN_STATE_MACHINE,
    CONFIG_APPLY_STATE_MACHINE,
    INCIDENT_STATE_MACHINE,
    ApprovalState,
    ChatTurnState,
    ConfigApplyState,
    IncidentState,
)


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (
            IncidentState.OPEN,
            {
                IncidentState.INVESTIGATING,
                IncidentState.AWAITING_APPROVAL,
                IncidentState.RESOLVED,
            },
        ),
        (
            IncidentState.INVESTIGATING,
            {
                IncidentState.AWAITING_APPROVAL,
                IncidentState.RESOLVED,
                IncidentState.CLOSED,
            },
        ),
        (
            IncidentState.AWAITING_APPROVAL,
            {
                IncidentState.INVESTIGATING,
                IncidentState.REMEDIATING,
                IncidentState.CLOSED,
            },
        ),
        (
            IncidentState.REMEDIATING,
            {
                IncidentState.INVESTIGATING,
                IncidentState.RESOLVED,
                IncidentState.CLOSED,
            },
        ),
        (IncidentState.RESOLVED, {IncidentState.CLOSED}),
        (IncidentState.CLOSED, set()),
    ],
)
def test_incident_state_machine_matches_phase_zero_spec(state, expected) -> None:
    assert INCIDENT_STATE_MACHINE.next_states(state) == expected


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (
            ChatTurnState.RUNNING,
            {
                ChatTurnState.WAITING_FOR_APPROVAL,
                ChatTurnState.COMPLETED,
                ChatTurnState.FAILED,
                ChatTurnState.CANCELLED,
            },
        ),
        (
            ChatTurnState.WAITING_FOR_APPROVAL,
            {
                ChatTurnState.RUNNING,
                ChatTurnState.FAILED,
                ChatTurnState.CANCELLED,
            },
        ),
        (ChatTurnState.COMPLETED, set()),
        (ChatTurnState.FAILED, set()),
        (ChatTurnState.CANCELLED, set()),
    ],
)
def test_chat_turn_state_machine_matches_phase_zero_spec(state, expected) -> None:
    assert CHAT_TURN_STATE_MACHINE.next_states(state) == expected


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (
            ApprovalState.PENDING,
            {
                ApprovalState.APPROVED,
                ApprovalState.REJECTED,
                ApprovalState.EXPIRED,
                ApprovalState.CANCELLED,
            },
        ),
        (
            ApprovalState.APPROVED,
            {
                ApprovalState.EXECUTED,
                ApprovalState.CANCELLED,
            },
        ),
        (ApprovalState.REJECTED, set()),
        (ApprovalState.EXPIRED, set()),
        (ApprovalState.CANCELLED, set()),
        (ApprovalState.EXECUTED, set()),
    ],
)
def test_approval_state_machine_matches_phase_zero_spec(state, expected) -> None:
    assert APPROVAL_STATE_MACHINE.next_states(state) == expected


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (
            ConfigApplyState.PENDING,
            {
                ConfigApplyState.VALIDATING,
                ConfigApplyState.CANCELLED,
            },
        ),
        (
            ConfigApplyState.VALIDATING,
            {
                ConfigApplyState.APPLYING,
                ConfigApplyState.FAILED,
                ConfigApplyState.CANCELLED,
            },
        ),
        (
            ConfigApplyState.APPLYING,
            {
                ConfigApplyState.SUCCEEDED,
                ConfigApplyState.FAILED,
            },
        ),
        (ConfigApplyState.SUCCEEDED, set()),
        (ConfigApplyState.FAILED, set()),
        (ConfigApplyState.CANCELLED, set()),
    ],
)
def test_config_apply_state_machine_matches_phase_zero_spec(state, expected) -> None:
    assert CONFIG_APPLY_STATE_MACHINE.next_states(state) == expected


@pytest.mark.parametrize(
    ("machine", "current_state", "target_state"),
    [
        (INCIDENT_STATE_MACHINE, IncidentState.OPEN, IncidentState.CLOSED),
        (
            CHAT_TURN_STATE_MACHINE,
            ChatTurnState.WAITING_FOR_APPROVAL,
            ChatTurnState.COMPLETED,
        ),
        (APPROVAL_STATE_MACHINE, ApprovalState.APPROVED, ApprovalState.REJECTED),
        (
            CONFIG_APPLY_STATE_MACHINE,
            ConfigApplyState.APPLYING,
            ConfigApplyState.CANCELLED,
        ),
    ],
)
def test_state_machines_reject_invalid_transitions(
    machine, current_state, target_state
) -> None:
    assert machine.can_transition(current_state, target_state) is False

    with pytest.raises(ValueError, match="invalid transition"):
        machine.require_transition(current_state, target_state)

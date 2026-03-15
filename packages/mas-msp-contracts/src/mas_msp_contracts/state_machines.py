"""Canonical MSP state transition matrices."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar

from .enums import (
    ApprovalState,
    ChatTurnState,
    ConfigApplyState,
    IncidentState,
)


StateT = TypeVar("StateT", bound=Enum)


@dataclass(frozen=True, slots=True)
class StateMachine(Generic[StateT]):
    """A small transition matrix wrapper for canonical workflows."""

    transitions: Mapping[StateT, frozenset[StateT]]

    def can_transition(self, current: StateT, target: StateT) -> bool:
        """Return whether a state transition is allowed."""

        return target in self.transitions[current]

    def next_states(self, current: StateT) -> frozenset[StateT]:
        """Return the allowed next states for the current state."""

        return self.transitions[current]

    def is_terminal(self, current: StateT) -> bool:
        """Return whether the state has no valid outgoing transitions."""

        return not self.transitions[current]

    def require_transition(self, current: StateT, target: StateT) -> None:
        """Raise a descriptive error for invalid transitions."""

        if not self.can_transition(current, target):
            raise ValueError(
                f"invalid transition from {current.value!r} to {target.value!r}"
            )


INCIDENT_STATE_MACHINE = StateMachine(
    transitions={
        IncidentState.OPEN: frozenset(
            {
                IncidentState.INVESTIGATING,
                IncidentState.AWAITING_APPROVAL,
                IncidentState.RESOLVED,
            }
        ),
        IncidentState.INVESTIGATING: frozenset(
            {
                IncidentState.AWAITING_APPROVAL,
                IncidentState.RESOLVED,
                IncidentState.CLOSED,
            }
        ),
        IncidentState.AWAITING_APPROVAL: frozenset(
            {
                IncidentState.INVESTIGATING,
                IncidentState.REMEDIATING,
                IncidentState.CLOSED,
            }
        ),
        IncidentState.REMEDIATING: frozenset(
            {
                IncidentState.INVESTIGATING,
                IncidentState.RESOLVED,
                IncidentState.CLOSED,
            }
        ),
        IncidentState.RESOLVED: frozenset({IncidentState.CLOSED}),
        IncidentState.CLOSED: frozenset(),
    }
)

CHAT_TURN_STATE_MACHINE = StateMachine(
    transitions={
        ChatTurnState.RUNNING: frozenset(
            {
                ChatTurnState.WAITING_FOR_APPROVAL,
                ChatTurnState.COMPLETED,
                ChatTurnState.FAILED,
                ChatTurnState.CANCELLED,
            }
        ),
        ChatTurnState.WAITING_FOR_APPROVAL: frozenset(
            {
                ChatTurnState.RUNNING,
                ChatTurnState.FAILED,
                ChatTurnState.CANCELLED,
            }
        ),
        ChatTurnState.COMPLETED: frozenset(),
        ChatTurnState.FAILED: frozenset(),
        ChatTurnState.CANCELLED: frozenset(),
    }
)

APPROVAL_STATE_MACHINE = StateMachine(
    transitions={
        ApprovalState.PENDING: frozenset(
            {
                ApprovalState.APPROVED,
                ApprovalState.REJECTED,
                ApprovalState.EXPIRED,
                ApprovalState.CANCELLED,
            }
        ),
        ApprovalState.APPROVED: frozenset(
            {ApprovalState.EXECUTED, ApprovalState.CANCELLED}
        ),
        ApprovalState.REJECTED: frozenset(),
        ApprovalState.EXPIRED: frozenset(),
        ApprovalState.CANCELLED: frozenset(),
        ApprovalState.EXECUTED: frozenset(),
    }
)

CONFIG_APPLY_STATE_MACHINE = StateMachine(
    transitions={
        ConfigApplyState.PENDING: frozenset(
            {ConfigApplyState.VALIDATING, ConfigApplyState.CANCELLED}
        ),
        ConfigApplyState.VALIDATING: frozenset(
            {
                ConfigApplyState.APPLYING,
                ConfigApplyState.FAILED,
                ConfigApplyState.CANCELLED,
            }
        ),
        ConfigApplyState.APPLYING: frozenset(
            {ConfigApplyState.SUCCEEDED, ConfigApplyState.FAILED}
        ),
        ConfigApplyState.SUCCEEDED: frozenset(),
        ConfigApplyState.FAILED: frozenset(),
        ConfigApplyState.CANCELLED: frozenset(),
    }
)

__all__ = [
    "APPROVAL_STATE_MACHINE",
    "CHAT_TURN_STATE_MACHINE",
    "CONFIG_APPLY_STATE_MACHINE",
    "INCIDENT_STATE_MACHINE",
    "StateMachine",
]

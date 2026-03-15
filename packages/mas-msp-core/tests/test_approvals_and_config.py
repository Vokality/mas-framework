from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

import pytest

from mas_msp_contracts import (
    ApprovalDecision,
    ApprovalDecisionValue,
    ApprovalRequested,
    ApprovalState,
    ConfigApplyRequested,
    ConfigDesiredState,
)
from mas_msp_core.approvals import (
    ApprovalCancellation,
    ApprovalController,
    ApprovalRecord,
)
from mas_msp_core.config import ConfigDeployerAgent


APPROVAL_ID = "11111111-1111-4111-8111-111111111111"
CLIENT_ID = "22222222-2222-4222-8222-222222222222"
FABRIC_ID = "33333333-3333-4333-8333-333333333333"
RUN_ID = "44444444-4444-4444-8444-444444444444"


def _approval_request() -> ApprovalRequested:
    now = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)
    return ApprovalRequested(
        approval_id=APPROVAL_ID,
        client_id=CLIENT_ID,
        fabric_id=FABRIC_ID,
        incident_id=None,
        action_kind="network.remediation",
        title="Bounce uplink",
        requested_at=now,
        expires_at=now + timedelta(hours=1),
        requested_by_agent="core-orchestrator",
        payload={},
        risk_summary="This mutates managed infrastructure.",
    )


def _approval_decision() -> ApprovalDecision:
    return ApprovalDecision(
        approval_id=APPROVAL_ID,
        decided_by_user_id="operator-1",
        decision=ApprovalDecisionValue.APPROVE,
        decided_at=datetime(2026, 3, 15, 12, 5, tzinfo=UTC),
        reason="Proceed",
    )


@dataclass(slots=True)
class FakeApprovalStore:
    approval: ApprovalRecord = field(
        default_factory=lambda: ApprovalRecord.from_requested(_approval_request())
    )
    cancelled_ids: list[str] = field(default_factory=list)
    executed_ids: list[str] = field(default_factory=list)

    async def create_request(
        self,
        request: ApprovalRequested,
    ) -> ApprovalRecord:
        self.approval = ApprovalRecord.from_requested(request)
        return self.approval

    async def get_request(self, approval_id: str) -> ApprovalRecord | None:
        if approval_id != self.approval.approval_id:
            return None
        return self.approval

    async def resolve_request(
        self,
        decision: ApprovalDecision,
    ) -> ApprovalRecord:
        self.approval = replace(
            self.approval,
            state=ApprovalState.APPROVED,
            decided_by_user_id=decision.decided_by_user_id,
            decision_reason=decision.reason,
            decided_at=decision.decided_at,
            approved_at=decision.decided_at,
        )
        return self.approval

    async def mark_executed(
        self,
        approval_id: str,
        *,
        executed_at: datetime,
    ) -> ApprovalRecord:
        self.executed_ids.append(approval_id)
        self.approval = replace(
            self.approval,
            state=ApprovalState.EXECUTED,
            executed_at=executed_at,
        )
        return self.approval

    async def cancel_request(
        self,
        cancellation: ApprovalCancellation,
    ) -> ApprovalRecord:
        self.cancelled_ids.append(cancellation.approval_id)
        self.approval = replace(
            self.approval,
            state=ApprovalState.CANCELLED,
            cancelled_at=cancellation.cancelled_at,
            decision_reason=cancellation.reason,
        )
        return self.approval

    async def expire_pending(self, *, now: datetime) -> list[ApprovalRecord]:
        raise NotImplementedError


@dataclass(slots=True)
class FakeOutcomeHandler:
    should_execute: bool
    approved_ids: list[str] = field(default_factory=list)

    async def on_approved(self, approval: ApprovalRecord) -> bool:
        self.approved_ids.append(approval.approval_id)
        return self.should_execute

    async def on_rejected(self, approval: ApprovalRecord) -> None:
        del approval
        return None

    async def on_expired(self, approval: ApprovalRecord) -> None:
        del approval
        return None

    async def on_cancelled(self, approval: ApprovalRecord) -> None:
        del approval
        return None


@pytest.mark.asyncio
async def test_approval_controller_leaves_unconsumed_approval_in_approved_state() -> (
    None
):
    store = FakeApprovalStore()
    controller = ApprovalController(
        store=store,
        outcome_handler=FakeOutcomeHandler(should_execute=False),
    )

    approval = await controller.apply_decision(_approval_decision())

    assert approval.state is ApprovalState.APPROVED
    assert store.executed_ids == []


@pytest.mark.asyncio
async def test_approval_controller_marks_consumed_approval_executed() -> None:
    store = FakeApprovalStore()
    controller = ApprovalController(
        store=store,
        outcome_handler=FakeOutcomeHandler(should_execute=True),
    )

    approval = await controller.apply_decision(_approval_decision())

    assert approval.state is ApprovalState.EXECUTED
    assert store.executed_ids == [APPROVAL_ID]


@pytest.mark.asyncio
async def test_approval_controller_cancels_pending_request() -> None:
    store = FakeApprovalStore()
    controller = ApprovalController(
        store=store,
        outcome_handler=FakeOutcomeHandler(should_execute=False),
    )

    approval = await controller.cancel_request(
        ApprovalCancellation(
            approval_id=APPROVAL_ID,
            cancelled_at=datetime(2026, 3, 15, 12, 6, tzinfo=UTC),
            actor_type="user",
            actor_id="operator-1",
            reason="Withdraw",
        )
    )

    assert approval.state is ApprovalState.CANCELLED
    assert store.cancelled_ids == [APPROVAL_ID]


@dataclass(slots=True)
class FakeDesiredStateStore:
    desired_state: ConfigDesiredState

    async def get_desired_state(self, client_id: str) -> ConfigDesiredState | None:
        assert client_id == self.desired_state.client_id
        return self.desired_state


@dataclass(slots=True)
class FakeRunStore:
    request: ConfigApplyRequested
    validation_results: list[object] = field(default_factory=list)

    async def get_apply_run(self, config_apply_run_id: str):
        del config_apply_run_id
        return None

    async def attach_approval(self, config_apply_run_id: str, *, approval_id: str):
        del config_apply_run_id, approval_id
        raise NotImplementedError

    async def start_validation(self, config_apply_run_id: str):
        del config_apply_run_id
        raise NotImplementedError

    async def start_apply(self, config_apply_run_id: str):
        del config_apply_run_id
        raise NotImplementedError

    async def complete_apply(self, config_apply_run_id: str):
        del config_apply_run_id
        raise NotImplementedError

    async def fail_apply(self, config_apply_run_id: str, *, error_summary: str):
        del config_apply_run_id, error_summary
        raise NotImplementedError

    async def cancel_apply(self, config_apply_run_id: str, *, reason: str):
        del config_apply_run_id, reason
        raise NotImplementedError

    async def record_apply_step(
        self,
        config_apply_run_id: str,
        *,
        step_name: str,
        outcome: str,
        details: dict[str, object],
    ) -> None:
        del config_apply_run_id, step_name, outcome, details
        raise NotImplementedError

    async def record_validation_result(self, result) -> None:  # noqa: ANN001
        self.validation_results.append(result)

    async def get_apply_request(
        self,
        config_apply_run_id: str,
    ) -> ConfigApplyRequested | None:
        assert config_apply_run_id == self.request.config_apply_run_id
        return self.request


@dataclass(slots=True)
class FakeApprovalController:
    async def request_approval(
        self, approval_request: ApprovalRequested
    ) -> ApprovalRecord:
        del approval_request
        raise NotImplementedError


@pytest.mark.asyncio
async def test_config_deployer_validation_marks_secret_like_keys_invalid() -> None:
    deployer = ConfigDeployerAgent(
        desired_state_store=FakeDesiredStateStore(
            desired_state=ConfigDesiredState(
                client_id=CLIENT_ID,
                fabric_id=FABRIC_ID,
                desired_state_version=7,
                tenant_metadata={"display_name": "Acme Corp"},
                policy={"password": "cleartext"},
                inventory_sources=[{"kind": "snmp"}],
                notification_routes=[{"kind": "email", "target": "noc@example.com"}],
            )
        ),
        run_store=FakeRunStore(
            request=ConfigApplyRequested(
                config_apply_run_id=RUN_ID,
                client_id=CLIENT_ID,
                desired_state_version=7,
                requested_by_user_id="admin-1",
                requested_at=datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
            )
        ),
        approval_controller=FakeApprovalController(),
    )

    result = await deployer.validate_run(RUN_ID)

    assert result.status == "invalid"
    assert "policy.password may contain only secret references." in result.errors

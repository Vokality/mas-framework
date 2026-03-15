"""Ops-plane approval persistence implementing the phase-4 core port."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update

from mas_msp_contracts import (
    APPROVAL_STATE_MACHINE,
    ApprovalDecision,
    ApprovalRequested,
)
from mas_msp_contracts import ApprovalDecisionValue, ApprovalState
from mas_msp_core.approvals import ApprovalCancellation, ApprovalRecord, ApprovalStore

from mas_ops_api.audit import AuditEntryInput, AuditService
from mas_ops_api.db.models import ApprovalRequestRecord
from mas_ops_api.db.session import Database
from mas_ops_api.streams.service import StreamService


class OpsPlaneApprovalStore(ApprovalStore):
    """Persist approval lifecycle transitions in the ops-plane datastore."""

    def __init__(
        self,
        *,
        database: Database,
        audit_service: AuditService,
        stream_service: StreamService,
    ) -> None:
        self._database = database
        self._audit_service = audit_service
        self._stream_service = stream_service

    async def create_request(
        self,
        request: ApprovalRequested,
    ) -> ApprovalRecord:
        stream_events = []
        async with self._database.session_factory() as session:
            existing = await session.get(ApprovalRequestRecord, request.approval_id)
            if existing is not None:
                return _record_from_model(existing)
            row = ApprovalRequestRecord(
                approval_id=request.approval_id,
                client_id=request.client_id,
                fabric_id=request.fabric_id,
                incident_id=request.incident_id,
                state=ApprovalState.PENDING.value,
                action_kind=request.action_kind,
                title=request.title,
                requested_at=request.requested_at,
                expires_at=request.expires_at,
                requested_by_agent=request.requested_by_agent,
                payload=dict(request.payload),
                risk_summary=request.risk_summary,
            )
            session.add(row)
            stream_events = [
                self._stream_service.build_event(
                    event_name="approval.requested",
                    client_id=row.client_id,
                    incident_id=row.incident_id,
                    chat_session_id=_chat_session_id(row.payload),
                    subject_type="approval_request",
                    subject_id=row.approval_id,
                    payload=_approval_payload(row),
                    occurred_at=row.requested_at,
                )
            ]
            session.add_all(stream_events)
            await self._audit_service.append_entry(
                session,
                AuditEntryInput(
                    client_id=row.client_id,
                    incident_id=row.incident_id,
                    approval_id=row.approval_id,
                    actor_type="agent",
                    actor_id=row.requested_by_agent,
                    target_type="approval_request",
                    target_id=row.approval_id,
                    action="approval.requested",
                    outcome=ApprovalState.PENDING.value,
                    details={"action_kind": row.action_kind, "title": row.title},
                    occurred_at=row.requested_at,
                ),
            )
            await session.flush()
            await session.commit()
            await session.refresh(row)
        for stream_event in stream_events:
            await self._stream_service.publish(stream_event)
        return _record_from_model(row)

    async def get_request(self, approval_id: str) -> ApprovalRecord | None:
        async with self._database.session_factory() as session:
            row = await session.get(ApprovalRequestRecord, approval_id)
            if row is None:
                return None
            return _record_from_model(row)

    async def resolve_request(
        self,
        decision: ApprovalDecision,
    ) -> ApprovalRecord:
        stream_events = []
        async with self._database.session_factory() as session:
            row = await session.get(ApprovalRequestRecord, decision.approval_id)
            if row is None:
                raise LookupError("approval request was not found")
            if row.state != ApprovalState.PENDING.value:
                raise ValueError("approval is not pending")
            if decision.decided_at.astimezone(UTC) > row.expires_at.astimezone(UTC):
                row.state = ApprovalState.EXPIRED.value
                row.expired_at = decision.decided_at
                stream_events = [
                    self._stream_service.build_event(
                        event_name="approval.expired",
                        client_id=row.client_id,
                        incident_id=row.incident_id,
                        chat_session_id=_chat_session_id(row.payload),
                        subject_type="approval_request",
                        subject_id=row.approval_id,
                        payload=_approval_payload(row),
                        occurred_at=decision.decided_at,
                    )
                ]
                session.add_all(stream_events)
                await self._audit_service.append_entry(
                    session,
                    AuditEntryInput(
                        client_id=row.client_id,
                        incident_id=row.incident_id,
                        approval_id=row.approval_id,
                        actor_type="system",
                        actor_id="approval-expiry",
                        target_type="approval_request",
                        target_id=row.approval_id,
                        action="approval.expired",
                        outcome=ApprovalState.EXPIRED.value,
                        occurred_at=decision.decided_at,
                    ),
                )
                await session.flush()
                await session.commit()
                raise ValueError("approval expired before the decision was applied")

            target_state = (
                ApprovalState.APPROVED
                if decision.decision is ApprovalDecisionValue.APPROVE
                else ApprovalState.REJECTED
            )
            APPROVAL_STATE_MACHINE.require_transition(
                ApprovalState(row.state), target_state
            )
            row.state = target_state.value
            row.decided_by_user_id = decision.decided_by_user_id
            row.decision_reason = decision.reason
            row.decided_at = decision.decided_at
            if target_state is ApprovalState.APPROVED:
                row.approved_at = decision.decided_at
            else:
                row.rejected_at = decision.decided_at

            stream_events = [
                self._stream_service.build_event(
                    event_name="approval.resolved",
                    client_id=row.client_id,
                    incident_id=row.incident_id,
                    chat_session_id=_chat_session_id(row.payload),
                    subject_type="approval_request",
                    subject_id=row.approval_id,
                    payload=_approval_payload(row),
                    occurred_at=decision.decided_at,
                )
            ]
            session.add_all(stream_events)
            await self._audit_service.append_entry(
                session,
                AuditEntryInput(
                    client_id=row.client_id,
                    incident_id=row.incident_id,
                    approval_id=row.approval_id,
                    actor_type="user",
                    actor_id=decision.decided_by_user_id,
                    target_type="approval_request",
                    target_id=row.approval_id,
                    action="approval.decision.recorded",
                    outcome=row.state,
                    details={"reason": decision.reason or ""},
                    occurred_at=decision.decided_at,
                ),
            )
            await session.flush()
            await session.commit()
            await session.refresh(row)
        for stream_event in stream_events:
            await self._stream_service.publish(stream_event)
        return _record_from_model(row)

    async def mark_executed(
        self,
        approval_id: str,
        *,
        executed_at: datetime,
    ) -> ApprovalRecord:
        stream_events = []
        async with self._database.session_factory() as session:
            row = await session.get(ApprovalRequestRecord, approval_id)
            if row is None:
                raise LookupError("approval request was not found")
            APPROVAL_STATE_MACHINE.require_transition(
                ApprovalState(row.state), ApprovalState.EXECUTED
            )
            row.state = ApprovalState.EXECUTED.value
            row.executed_at = executed_at
            stream_events = [
                self._stream_service.build_event(
                    event_name="approval.executed",
                    client_id=row.client_id,
                    incident_id=row.incident_id,
                    chat_session_id=_chat_session_id(row.payload),
                    subject_type="approval_request",
                    subject_id=row.approval_id,
                    payload=_approval_payload(row),
                    occurred_at=executed_at,
                )
            ]
            session.add_all(stream_events)
            await self._audit_service.append_entry(
                session,
                AuditEntryInput(
                    client_id=row.client_id,
                    incident_id=row.incident_id,
                    approval_id=row.approval_id,
                    actor_type="agent",
                    actor_id=row.requested_by_agent,
                    target_type="approval_request",
                    target_id=row.approval_id,
                    action="approval.executed",
                    outcome=ApprovalState.EXECUTED.value,
                    occurred_at=executed_at,
                ),
            )
            await session.flush()
            await session.commit()
            await session.refresh(row)
        for stream_event in stream_events:
            await self._stream_service.publish(stream_event)
        return _record_from_model(row)

    async def cancel_request(
        self,
        cancellation: ApprovalCancellation,
    ) -> ApprovalRecord:
        stream_events = []
        async with self._database.session_factory() as session:
            row = await session.get(
                ApprovalRequestRecord,
                cancellation.approval_id,
            )
            if row is None:
                raise LookupError("approval request was not found")
            APPROVAL_STATE_MACHINE.require_transition(
                ApprovalState(row.state), ApprovalState.CANCELLED
            )
            row.state = ApprovalState.CANCELLED.value
            row.cancelled_at = cancellation.cancelled_at
            row.decision_reason = cancellation.reason
            stream_events = [
                self._stream_service.build_event(
                    event_name="approval.cancelled",
                    client_id=row.client_id,
                    incident_id=row.incident_id,
                    chat_session_id=_chat_session_id(row.payload),
                    subject_type="approval_request",
                    subject_id=row.approval_id,
                    payload=_approval_payload(row),
                    occurred_at=cancellation.cancelled_at,
                )
            ]
            session.add_all(stream_events)
            await self._audit_service.append_entry(
                session,
                AuditEntryInput(
                    client_id=row.client_id,
                    incident_id=row.incident_id,
                    approval_id=row.approval_id,
                    actor_type=cancellation.actor_type,
                    actor_id=cancellation.actor_id,
                    target_type="approval_request",
                    target_id=row.approval_id,
                    action="approval.cancelled",
                    outcome=ApprovalState.CANCELLED.value,
                    details={"reason": cancellation.reason or ""},
                    occurred_at=cancellation.cancelled_at,
                ),
            )
            await session.flush()
            await session.commit()
            await session.refresh(row)
        for stream_event in stream_events:
            await self._stream_service.publish(stream_event)
        return _record_from_model(row)

    async def expire_pending(self, *, now: datetime) -> list[ApprovalRecord]:
        stream_events = []
        async with self._database.session_factory() as session:
            claimed_ids = list(
                (
                    await session.scalars(
                        update(ApprovalRequestRecord)
                        .where(
                            ApprovalRequestRecord.state == ApprovalState.PENDING.value,
                            ApprovalRequestRecord.expires_at < now.astimezone(UTC),
                        )
                        .values(
                            state=ApprovalState.EXPIRED.value,
                            expired_at=now,
                        )
                        .returning(ApprovalRequestRecord.approval_id)
                    )
                ).all()
            )
            if not claimed_ids:
                await session.commit()
                return []
            rows = list(
                (
                    await session.scalars(
                        select(ApprovalRequestRecord)
                        .where(ApprovalRequestRecord.approval_id.in_(claimed_ids))
                        .order_by(ApprovalRequestRecord.requested_at.asc())
                    )
                ).all()
            )
            expired: list[ApprovalRecord] = []
            for row in rows:
                stream_event = self._stream_service.build_event(
                    event_name="approval.expired",
                    client_id=row.client_id,
                    incident_id=row.incident_id,
                    chat_session_id=_chat_session_id(row.payload),
                    subject_type="approval_request",
                    subject_id=row.approval_id,
                    payload=_approval_payload(row),
                    occurred_at=now,
                )
                session.add(stream_event)
                stream_events.append(stream_event)
                await self._audit_service.append_entry(
                    session,
                    AuditEntryInput(
                        client_id=row.client_id,
                        incident_id=row.incident_id,
                        approval_id=row.approval_id,
                        actor_type="system",
                        actor_id="approval-expiry",
                        target_type="approval_request",
                        target_id=row.approval_id,
                        action="approval.expired",
                        outcome=ApprovalState.EXPIRED.value,
                        occurred_at=now,
                    ),
                )
                expired.append(_record_from_model(row))
            await session.flush()
            await session.commit()
        for stream_event in stream_events:
            await self._stream_service.publish(stream_event)
        return expired


def _record_from_model(row: ApprovalRequestRecord) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=row.approval_id,
        client_id=row.client_id,
        fabric_id=row.fabric_id,
        incident_id=row.incident_id,
        state=ApprovalState(row.state),
        action_kind=row.action_kind,
        title=row.title,
        requested_at=row.requested_at,
        expires_at=row.expires_at,
        requested_by_agent=row.requested_by_agent,
        payload=dict(row.payload),
        risk_summary=row.risk_summary,
        decided_by_user_id=row.decided_by_user_id,
        decision_reason=row.decision_reason,
        decided_at=row.decided_at,
        approved_at=row.approved_at,
        rejected_at=row.rejected_at,
        expired_at=row.expired_at,
        cancelled_at=row.cancelled_at,
        executed_at=row.executed_at,
    )


def _approval_payload(row: ApprovalRequestRecord) -> dict[str, object]:
    return {
        "approval_id": row.approval_id,
        "state": row.state,
        "action_kind": row.action_kind,
        "title": row.title,
        "incident_id": row.incident_id,
        "payload": dict(row.payload),
    }


def _chat_session_id(payload: dict[str, object]) -> str | None:
    chat_session_id = payload.get("chat_session_id")
    if isinstance(chat_session_id, str) and chat_session_id:
        return chat_session_id
    return None


__all__ = ["OpsPlaneApprovalStore"]

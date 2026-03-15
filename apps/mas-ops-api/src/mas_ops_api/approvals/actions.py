"""Deferred approval action handlers for incident remediation and config apply."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from mas_msp_contracts import IncidentState
from mas_msp_core import ApprovalExecutionOutcome
from mas_msp_core.approvals import ApprovalRecord
from mas_msp_core.config import ConfigDeployerAgent

from mas_ops_api.chat.service import ChatService
from mas_ops_api.connectors import ConnectorRegistry
from mas_ops_api.db.models import ChatSession
from mas_ops_api.db.session import Database
from mas_ops_api.incidents.service import IncidentProjectionService
from mas_ops_api.projections.repository import PortfolioQueries
from mas_ops_api.streams.service import StreamService

from .payloads import IncidentRemediationApprovalPayload


class ApprovalActionHandler(Protocol):
    """Handle deferred side effects for one approval action kind."""

    async def on_approved(
        self,
        approval: ApprovalRecord,
    ) -> ApprovalExecutionOutcome: ...

    async def on_rejected(self, approval: ApprovalRecord) -> None: ...

    async def on_expired(self, approval: ApprovalRecord) -> None: ...

    async def on_cancelled(self, approval: ApprovalRecord) -> None: ...


class ApprovalActionRouter:
    """Dispatch approval outcomes to the registered action-specific handler."""

    def __init__(self) -> None:
        self._handlers: dict[str, ApprovalActionHandler] = {}

    def register(self, action_kind: str, handler: ApprovalActionHandler) -> None:
        """Register the handler for one approval action kind."""

        self._handlers[action_kind] = handler

    async def on_approved(
        self,
        approval: ApprovalRecord,
    ) -> ApprovalExecutionOutcome:
        return await self._handler_for(approval).on_approved(approval)

    async def on_rejected(self, approval: ApprovalRecord) -> None:
        await self._handler_for(approval).on_rejected(approval)

    async def on_expired(self, approval: ApprovalRecord) -> None:
        await self._handler_for(approval).on_expired(approval)

    async def on_cancelled(self, approval: ApprovalRecord) -> None:
        await self._handler_for(approval).on_cancelled(approval)

    def _handler_for(self, approval: ApprovalRecord) -> ApprovalActionHandler:
        try:
            return self._handlers[approval.action_kind]
        except KeyError as exc:
            raise LookupError(
                f"no approval action handler is registered for {approval.action_kind!r}"
            ) from exc


@dataclass(slots=True)
class IncidentRemediationActionHandler:
    """Resume or unwind incident-scoped remediation requests."""

    database: Database
    chat_service: ChatService
    incident_projection_service: IncidentProjectionService
    stream_service: StreamService

    async def on_approved(
        self,
        approval: ApprovalRecord,
    ) -> ApprovalExecutionOutcome:
        payload = IncidentRemediationApprovalPayload.from_payload(approval.payload)
        if payload is None:
            return ApprovalExecutionOutcome(executed=False)
        await self._transition_incident(
            approval,
            payload=payload,
            target_state=IncidentState.REMEDIATING,
            activity_event_type="remediation.executed",
            activity_payload={
                "approval_id": approval.approval_id,
                "action_kind": approval.action_kind,
                "title": approval.title,
                "requested_action": payload.requested_action,
            },
        )
        await self._complete_waiting_turn(
            payload,
            approval_id=approval.approval_id,
            message=(
                "Approval approved. The requested remediation action has been executed."
            ),
        )
        return ApprovalExecutionOutcome(
            executed=True,
            executed_at=approval.approved_at,
        )

    async def on_rejected(self, approval: ApprovalRecord) -> None:
        await self._cancel_waiting_remediation(
            approval,
            status_message="Approval rejected. The remediation action was not executed.",
            activity_event_type="remediation.rejected",
        )

    async def on_expired(self, approval: ApprovalRecord) -> None:
        await self._cancel_waiting_remediation(
            approval,
            status_message="Approval expired before the remediation action could run.",
            activity_event_type="remediation.expired",
        )

    async def on_cancelled(self, approval: ApprovalRecord) -> None:
        await self._cancel_waiting_remediation(
            approval,
            status_message="The remediation action was cancelled before execution.",
            activity_event_type="remediation.cancelled",
        )

    async def _cancel_waiting_remediation(
        self,
        approval: ApprovalRecord,
        *,
        status_message: str,
        activity_event_type: str,
    ) -> None:
        payload = IncidentRemediationApprovalPayload.from_payload(approval.payload)
        if payload is None:
            return
        await self._transition_incident(
            approval,
            payload=payload,
            target_state=IncidentState.INVESTIGATING,
            activity_event_type=activity_event_type,
            activity_payload={
                "approval_id": approval.approval_id,
                "action_kind": approval.action_kind,
                "title": approval.title,
                "requested_action": payload.requested_action,
            },
            only_if_waiting=True,
        )
        await self._cancel_waiting_turn(
            payload,
            approval_id=approval.approval_id,
            message=status_message,
        )

    async def _transition_incident(
        self,
        approval: ApprovalRecord,
        *,
        payload: IncidentRemediationApprovalPayload,
        target_state: IncidentState,
        activity_event_type: str,
        activity_payload: dict[str, object],
        only_if_waiting: bool = False,
    ) -> None:
        stream_events = []
        async with self.database.session_factory() as session:
            incident = await PortfolioQueries.get_incident(session, payload.incident_id)
            if incident is None:
                raise LookupError("incident for deferred approval was not found")
            asset_ids = payload.asset_ids or [
                asset.asset_id
                for asset in await PortfolioQueries.list_assets_for_incident(
                    session, payload.incident_id
                )
            ]
            current_state = IncidentState(incident.state)
            next_state = (
                current_state
                if only_if_waiting
                and current_state is not IncidentState.AWAITING_APPROVAL
                else target_state
            )
            (
                _activity,
                activity_events,
            ) = await self.incident_projection_service.append_activity(
                session,
                incident_id=payload.incident_id,
                event_type=activity_event_type,
                payload=activity_payload,
                asset_id=asset_ids[0] if asset_ids else None,
                source_event_id=f"approval:{approval.approval_id}:{activity_event_type}",
            )
            (
                updated_incident,
                incident_events,
            ) = await self.incident_projection_service.persist_summary(
                session,
                incident_id=payload.incident_id,
                summary=incident.summary,
                severity=incident.severity,
                state=next_state,
                recommended_actions=list(incident.recommended_actions),
                asset_ids=asset_ids,
                activity_payload={
                    "approval_id": approval.approval_id,
                    "state": next_state.value,
                },
                activity_source_event_id=f"approval:{approval.approval_id}:summary",
            )
            del updated_incident
            stream_events = [*activity_events, *incident_events]
            session.add_all(stream_events)
            await session.flush()
            await session.commit()
        for stream_event in stream_events:
            await self.stream_service.publish(stream_event)

    async def _complete_waiting_turn(
        self,
        payload: IncidentRemediationApprovalPayload,
        *,
        approval_id: str,
        message: str,
    ) -> None:
        async with self.database.session_factory() as session:
            chat_session = await session.get(ChatSession, payload.chat_session_id)
            if chat_session is None:
                raise LookupError("chat session for deferred approval was not found")
            await self.chat_service.complete_waiting_turn(
                session,
                chat_session=chat_session,
                turn_id=payload.turn_id,
                assistant_content=message,
                stream_service=self.stream_service,
                approval_id=approval_id,
            )

    async def _cancel_waiting_turn(
        self,
        payload: IncidentRemediationApprovalPayload,
        *,
        approval_id: str,
        message: str,
    ) -> None:
        async with self.database.session_factory() as session:
            chat_session = await session.get(ChatSession, payload.chat_session_id)
            if chat_session is None:
                return
            await self.chat_service.cancel_waiting_turn(
                session,
                chat_session=chat_session,
                turn_id=payload.turn_id,
                assistant_content=message,
                stream_service=self.stream_service,
                approval_id=approval_id,
            )


@dataclass(slots=True)
class ConfigApplyActionHandler:
    """Resume or unwind desired-state apply runs after an approval decision."""

    config_deployer: ConfigDeployerAgent

    async def on_approved(
        self,
        approval: ApprovalRecord,
    ) -> ApprovalExecutionOutcome:
        result = await self.config_deployer.execute_approved_apply(approval)
        return ApprovalExecutionOutcome(
            executed=True,
            executed_at=result.completed_at or result.started_at,
        )

    async def on_rejected(self, approval: ApprovalRecord) -> None:
        await self.config_deployer.reject_apply(approval)

    async def on_expired(self, approval: ApprovalRecord) -> None:
        await self.config_deployer.reject_apply(approval)

    async def on_cancelled(self, approval: ApprovalRecord) -> None:
        await self.config_deployer.cancel_apply(approval)


@dataclass(slots=True)
class HostRemediationActionHandler:
    """Resume approved host remediations through the per-client connector path."""

    command_connector_registry: ConnectorRegistry
    chat_service: ChatService
    database: Database
    incident_projection_service: IncidentProjectionService
    stream_service: StreamService

    async def on_approved(
        self,
        approval: ApprovalRecord,
    ) -> ApprovalExecutionOutcome:
        payload = IncidentRemediationApprovalPayload.from_payload(approval.payload)
        if payload is None or payload.remediation_execute is None:
            return ApprovalExecutionOutcome(executed=False)
        execution = await self.command_connector_registry.get(
            approval.client_id
        ).dispatch_approved_remediation(
            approval_id=approval.approval_id,
            remediation=payload.remediation_execute,
        )
        await self._complete_waiting_turn(
            payload,
            approval_id=approval.approval_id,
            message=execution.operator_message,
        )
        return ApprovalExecutionOutcome(
            executed=True,
            executed_at=execution.remediation_result.completed_at,
        )

    async def on_rejected(self, approval: ApprovalRecord) -> None:
        await self._cancel_waiting_remediation(
            approval,
            status_message="Approval rejected. The host remediation was not executed.",
            activity_event_type="remediation.rejected",
        )

    async def on_expired(self, approval: ApprovalRecord) -> None:
        await self._cancel_waiting_remediation(
            approval,
            status_message="Approval expired before the host remediation could run.",
            activity_event_type="remediation.expired",
        )

    async def on_cancelled(self, approval: ApprovalRecord) -> None:
        await self._cancel_waiting_remediation(
            approval,
            status_message="The host remediation was cancelled before execution.",
            activity_event_type="remediation.cancelled",
        )

    async def _cancel_waiting_remediation(
        self,
        approval: ApprovalRecord,
        *,
        status_message: str,
        activity_event_type: str,
    ) -> None:
        payload = IncidentRemediationApprovalPayload.from_payload(approval.payload)
        if payload is None:
            return
        await self._transition_incident(
            approval,
            payload=payload,
            target_state=IncidentState.INVESTIGATING,
            activity_event_type=activity_event_type,
            activity_payload={
                "approval_id": approval.approval_id,
                "action_kind": approval.action_kind,
                "title": approval.title,
                "requested_action": payload.requested_action,
            },
            only_if_waiting=True,
        )
        await self._cancel_waiting_turn(
            payload,
            approval_id=approval.approval_id,
            message=status_message,
        )

    async def _transition_incident(
        self,
        approval: ApprovalRecord,
        *,
        payload: IncidentRemediationApprovalPayload,
        target_state: IncidentState,
        activity_event_type: str,
        activity_payload: dict[str, object],
        only_if_waiting: bool = False,
    ) -> None:
        stream_events = []
        async with self.database.session_factory() as session:
            incident = await PortfolioQueries.get_incident(session, payload.incident_id)
            if incident is None:
                raise LookupError("incident for deferred approval was not found")
            asset_ids = payload.asset_ids or [
                asset.asset_id
                for asset in await PortfolioQueries.list_assets_for_incident(
                    session, payload.incident_id
                )
            ]
            current_state = IncidentState(incident.state)
            next_state = (
                current_state
                if only_if_waiting
                and current_state is not IncidentState.AWAITING_APPROVAL
                else target_state
            )
            (
                _activity,
                activity_events,
            ) = await self.incident_projection_service.append_activity(
                session,
                incident_id=payload.incident_id,
                event_type=activity_event_type,
                payload=activity_payload,
                asset_id=asset_ids[0] if asset_ids else None,
                source_event_id=f"approval:{approval.approval_id}:{activity_event_type}",
            )
            (
                _updated_incident,
                incident_events,
            ) = await self.incident_projection_service.persist_summary(
                session,
                incident_id=payload.incident_id,
                summary=incident.summary,
                severity=incident.severity,
                state=next_state,
                recommended_actions=list(incident.recommended_actions),
                asset_ids=asset_ids,
                activity_payload={
                    "approval_id": approval.approval_id,
                    "state": next_state.value,
                },
                activity_source_event_id=f"approval:{approval.approval_id}:summary",
            )
            stream_events = [*activity_events, *incident_events]
            session.add_all(stream_events)
            await session.flush()
            await session.commit()
        for stream_event in stream_events:
            await self.stream_service.publish(stream_event)

    async def _complete_waiting_turn(
        self,
        payload: IncidentRemediationApprovalPayload,
        *,
        approval_id: str,
        message: str,
    ) -> None:
        async with self.database.session_factory() as session:
            chat_session = await session.get(ChatSession, payload.chat_session_id)
            if chat_session is None:
                raise LookupError("chat session for deferred approval was not found")
            await self.chat_service.complete_waiting_turn(
                session,
                chat_session=chat_session,
                turn_id=payload.turn_id,
                assistant_content=message,
                stream_service=self.stream_service,
                approval_id=approval_id,
            )

    async def _cancel_waiting_turn(
        self,
        payload: IncidentRemediationApprovalPayload,
        *,
        approval_id: str,
        message: str,
    ) -> None:
        async with self.database.session_factory() as session:
            chat_session = await session.get(ChatSession, payload.chat_session_id)
            if chat_session is None:
                return
            await self.chat_service.cancel_waiting_turn(
                session,
                chat_session=chat_session,
                turn_id=payload.turn_id,
                assistant_content=message,
                stream_service=self.stream_service,
                approval_id=approval_id,
            )


__all__ = [
    "ApprovalActionHandler",
    "ApprovalActionRouter",
    "ConfigApplyActionHandler",
    "HostRemediationActionHandler",
    "IncidentRemediationActionHandler",
]

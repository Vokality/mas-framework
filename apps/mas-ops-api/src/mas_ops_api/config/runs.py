"""Config validation/apply run creation, history, and deployer stores."""

from __future__ import annotations

from collections import defaultdict
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mas_msp_contracts import (
    CONFIG_APPLY_STATE_MACHINE,
    ConfigApplyRequested,
    ConfigApplyResult,
    ConfigApplyState,
    ConfigValidationResult,
)
from mas_msp_core.config import ConfigApplyRunRecord, ConfigRunStore

from mas_ops_api.audit import AuditEntryInput, AuditService
from mas_ops_api.db.base import utc_now
from mas_ops_api.db.models import (
    ConfigApplyRun,
    ConfigApplyStepRecord,
    ConfigDesiredStateRecord,
    ConfigValidationRun,
)
from mas_ops_api.db.session import Database
from mas_ops_api.streams.service import StreamService

from .types import ConfigRunHistory


class ConfigRunService:
    """Create config validation and apply requests and list their history."""

    def __init__(
        self,
        *,
        audit_service: AuditService,
        stream_service: StreamService,
    ) -> None:
        self._audit_service = audit_service
        self._stream_service = stream_service

    async def create_validation_run(
        self,
        session: AsyncSession,
        *,
        client_id: str,
        requested_by_user_id: str,
    ) -> ConfigValidationRun:
        """Create a validation request against the latest desired state."""

        desired_state = await session.get(ConfigDesiredStateRecord, client_id)
        if desired_state is None:
            raise LookupError("desired-state config not found")

        run = ConfigValidationRun(
            config_apply_run_id=str(uuid4()),
            client_id=client_id,
            desired_state_version=desired_state.desired_state_version,
            status="pending",
            errors=[],
            warnings=[],
            requested_by_user_id=requested_by_user_id,
            requested_at=utc_now(),
            validated_at=utc_now(),
        )
        session.add(run)
        stream_event = self._stream_service.build_event(
            event_name="client.updated",
            client_id=client_id,
            incident_id=None,
            chat_session_id=None,
            subject_type="config_validation_run",
            subject_id=run.config_apply_run_id,
            payload={
                "config_apply_run_id": run.config_apply_run_id,
                "desired_state_version": run.desired_state_version,
                "status": run.status,
            },
            occurred_at=run.requested_at,
        )
        session.add(stream_event)
        await self._audit_service.append_entry(
            session,
            AuditEntryInput(
                client_id=client_id,
                config_apply_run_id=run.config_apply_run_id,
                actor_type="user",
                actor_id=requested_by_user_id,
                target_type="config_validation_run",
                target_id=run.config_apply_run_id,
                action="config.validation.requested",
                outcome="pending",
                details={"desired_state_version": run.desired_state_version},
                occurred_at=run.requested_at,
            ),
        )
        await session.commit()
        await session.refresh(run)
        await session.refresh(stream_event)
        await self._stream_service.publish(stream_event)
        return run

    async def create_apply_run(
        self,
        session: AsyncSession,
        *,
        client_id: str,
        requested_by_user_id: str,
    ) -> ConfigApplyRun:
        """Create a pending apply run against the latest desired-state version."""

        desired_state = await session.get(ConfigDesiredStateRecord, client_id)
        if desired_state is None:
            raise LookupError("desired-state config not found")

        run = ConfigApplyRun(
            config_apply_run_id=str(uuid4()),
            client_id=client_id,
            desired_state_version=desired_state.desired_state_version,
            status=ConfigApplyState.PENDING.value,
            requested_by_user_id=requested_by_user_id,
            requested_at=utc_now(),
        )
        session.add(run)
        stream_event = self._stream_service.build_event(
            event_name="client.updated",
            client_id=client_id,
            incident_id=None,
            chat_session_id=None,
            subject_type="config_apply_run",
            subject_id=run.config_apply_run_id,
            payload=_apply_result_payload(run),
            occurred_at=run.requested_at,
        )
        session.add(stream_event)
        await self._audit_service.append_entry(
            session,
            AuditEntryInput(
                client_id=client_id,
                config_apply_run_id=run.config_apply_run_id,
                actor_type="user",
                actor_id=requested_by_user_id,
                target_type="config_apply_run",
                target_id=run.config_apply_run_id,
                action="config.apply.requested",
                outcome=ConfigApplyState.PENDING.value,
                details={"desired_state_version": run.desired_state_version},
                occurred_at=run.requested_at,
            ),
        )
        await session.commit()
        await session.refresh(run)
        await session.refresh(stream_event)
        await self._stream_service.publish(stream_event)
        return run

    async def list_runs(
        self,
        session: AsyncSession,
        *,
        client_id: str,
    ) -> ConfigRunHistory:
        """Return validation/apply history with grouped step progress."""

        validation_runs = list(
            (
                await session.scalars(
                    select(ConfigValidationRun)
                    .where(ConfigValidationRun.client_id == client_id)
                    .order_by(ConfigValidationRun.requested_at.desc())
                )
            ).all()
        )
        apply_runs = list(
            (
                await session.scalars(
                    select(ConfigApplyRun)
                    .where(ConfigApplyRun.client_id == client_id)
                    .order_by(ConfigApplyRun.requested_at.desc())
                )
            ).all()
        )
        rows = list(
            (
                await session.scalars(
                    select(ConfigApplyStepRecord)
                    .where(ConfigApplyStepRecord.client_id == client_id)
                    .order_by(
                        ConfigApplyStepRecord.config_apply_run_id.asc(),
                        ConfigApplyStepRecord.step_index.asc(),
                    )
                )
            ).all()
        )
        grouped_steps: dict[str, list[ConfigApplyStepRecord]] = defaultdict(list)
        for row in rows:
            grouped_steps[row.config_apply_run_id].append(row)
        return ConfigRunHistory(
            validation_runs=validation_runs,
            apply_runs=apply_runs,
            apply_steps=dict(grouped_steps),
        )

    async def get_apply_run(
        self,
        session: AsyncSession,
        *,
        client_id: str,
        config_apply_run_id: str,
    ) -> ConfigApplyRun | None:
        """Return one apply run for the target client."""

        row = await session.get(ConfigApplyRun, config_apply_run_id)
        if row is None or row.client_id != client_id:
            return None
        return row

    async def cancel_apply_run(
        self,
        session: AsyncSession,
        *,
        client_id: str,
        config_apply_run_id: str,
        actor_user_id: str,
        reason: str,
    ) -> ConfigApplyResult:
        """Cancel a locally owned apply run when no live approval remains."""

        row = await self.get_apply_run(
            session,
            client_id=client_id,
            config_apply_run_id=config_apply_run_id,
        )
        if row is None:
            raise LookupError("config apply run was not found")
        CONFIG_APPLY_STATE_MACHINE.require_transition(
            ConfigApplyState(row.status),
            ConfigApplyState.CANCELLED,
        )
        now = utc_now()
        row.status = ConfigApplyState.CANCELLED.value
        row.completed_at = now
        row.error_summary = reason
        result = _apply_result(row)
        stream_event = self._stream_service.build_event(
            event_name="client.updated",
            client_id=row.client_id,
            incident_id=None,
            chat_session_id=None,
            subject_type="config_apply_run",
            subject_id=row.config_apply_run_id,
            payload=_apply_result_payload(row),
            occurred_at=now,
        )
        session.add(stream_event)
        await self._audit_service.append_entry(
            session,
            AuditEntryInput(
                client_id=row.client_id,
                config_apply_run_id=row.config_apply_run_id,
                approval_id=row.approval_id,
                actor_type="user",
                actor_id=actor_user_id,
                target_type="config_apply_run",
                target_id=row.config_apply_run_id,
                action="config.apply.cancelled",
                outcome=ConfigApplyState.CANCELLED.value,
                details={
                    "desired_state_version": row.desired_state_version,
                    "error_summary": reason,
                },
                occurred_at=now,
            ),
        )
        await session.commit()
        await session.refresh(row)
        await session.refresh(stream_event)
        await self._stream_service.publish(stream_event)
        return result


class OpsPlaneConfigRunStore(ConfigRunStore):
    """Database-backed config run store for the phase-4 deployer."""

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

    async def get_apply_run(
        self,
        config_apply_run_id: str,
    ) -> ConfigApplyRunRecord | None:
        async with self._database.session_factory() as session:
            row = await session.get(ConfigApplyRun, config_apply_run_id)
            if row is None:
                return None
            return _apply_run_record(row)

    async def attach_approval(
        self,
        config_apply_run_id: str,
        *,
        approval_id: str,
    ) -> ConfigApplyRunRecord:
        stream_events = []
        async with self._database.session_factory() as session:
            row = await _require_apply_run(session, config_apply_run_id)
            row.approval_id = approval_id
            stream_events = [
                self._build_apply_stream_event(
                    row,
                    payload={
                        "approval_id": approval_id,
                        "config_apply_run_id": row.config_apply_run_id,
                        "desired_state_version": row.desired_state_version,
                        "status": row.status,
                    },
                    occurred_at=utc_now(),
                )
            ]
            session.add_all(stream_events)
            await session.flush()
            await session.commit()
            await session.refresh(row)
        for stream_event in stream_events:
            await self._stream_service.publish(stream_event)
        return _apply_run_record(row)

    async def start_validation(
        self,
        config_apply_run_id: str,
    ) -> ConfigApplyRunRecord:
        stream_events = []
        async with self._database.session_factory() as session:
            row = await _require_apply_run(session, config_apply_run_id)
            CONFIG_APPLY_STATE_MACHINE.require_transition(
                ConfigApplyState(row.status), ConfigApplyState.VALIDATING
            )
            row.status = ConfigApplyState.VALIDATING.value
            row.started_at = row.started_at or utc_now()
            stream_events = [
                self._build_apply_stream_event(
                    row,
                    payload=_apply_result_payload(row),
                    occurred_at=row.started_at,
                )
            ]
            session.add_all(stream_events)
            await session.flush()
            await session.commit()
            await session.refresh(row)
        for stream_event in stream_events:
            await self._stream_service.publish(stream_event)
        return _apply_run_record(row)

    async def start_apply(
        self,
        config_apply_run_id: str,
    ) -> ConfigApplyRunRecord:
        stream_events = []
        async with self._database.session_factory() as session:
            row = await _require_apply_run(session, config_apply_run_id)
            CONFIG_APPLY_STATE_MACHINE.require_transition(
                ConfigApplyState(row.status), ConfigApplyState.APPLYING
            )
            row.status = ConfigApplyState.APPLYING.value
            row.started_at = row.started_at or utc_now()
            stream_events = [
                self._build_apply_stream_event(
                    row,
                    payload=_apply_result_payload(row),
                    occurred_at=row.started_at,
                )
            ]
            session.add_all(stream_events)
            await session.flush()
            await session.commit()
            await session.refresh(row)
        for stream_event in stream_events:
            await self._stream_service.publish(stream_event)
        return _apply_run_record(row)

    async def complete_apply(
        self,
        config_apply_run_id: str,
    ) -> ConfigApplyResult:
        return await self._finish_apply(
            config_apply_run_id,
            target_state=ConfigApplyState.SUCCEEDED,
            error_summary=None,
            audit_action="config.apply.completed",
            audit_outcome=ConfigApplyState.SUCCEEDED.value,
        )

    async def fail_apply(
        self,
        config_apply_run_id: str,
        *,
        error_summary: str,
    ) -> ConfigApplyResult:
        return await self._finish_apply(
            config_apply_run_id,
            target_state=ConfigApplyState.FAILED,
            error_summary=error_summary,
            audit_action="config.apply.completed",
            audit_outcome=ConfigApplyState.FAILED.value,
        )

    async def cancel_apply(
        self,
        config_apply_run_id: str,
        *,
        reason: str,
    ) -> ConfigApplyResult:
        return await self._finish_apply(
            config_apply_run_id,
            target_state=ConfigApplyState.CANCELLED,
            error_summary=reason,
            audit_action="config.apply.cancelled",
            audit_outcome=ConfigApplyState.CANCELLED.value,
        )

    async def record_apply_step(
        self,
        config_apply_run_id: str,
        *,
        step_name: str,
        outcome: str,
        details: dict[str, object],
    ) -> None:
        stream_events = []
        async with self._database.session_factory() as session:
            row = await _require_apply_run(session, config_apply_run_id)
            step_index = int(
                (
                    await session.scalar(
                        select(func.count())
                        .select_from(ConfigApplyStepRecord)
                        .where(
                            ConfigApplyStepRecord.config_apply_run_id
                            == config_apply_run_id
                        )
                    )
                )
                or 0
            )
            step = ConfigApplyStepRecord(
                config_apply_run_id=config_apply_run_id,
                client_id=row.client_id,
                step_index=step_index,
                step_name=step_name,
                outcome=outcome,
                details=dict(details),
                occurred_at=utc_now(),
            )
            session.add(step)
            stream_events = [
                self._stream_service.build_event(
                    event_name="client.updated",
                    client_id=row.client_id,
                    incident_id=None,
                    chat_session_id=None,
                    subject_type="config_apply_step",
                    subject_id=str(step_index),
                    payload={
                        "config_apply_run_id": config_apply_run_id,
                        "step_index": step_index,
                        "step_name": step_name,
                        "outcome": outcome,
                    },
                    occurred_at=step.occurred_at,
                )
            ]
            session.add_all(stream_events)
            await session.flush()
            await session.commit()
        for stream_event in stream_events:
            await self._stream_service.publish(stream_event)

    async def record_validation_result(
        self,
        result: ConfigValidationResult,
    ) -> None:
        stream_events = []
        async with self._database.session_factory() as session:
            validation_row = await session.get(
                ConfigValidationRun, result.config_apply_run_id
            )
            if validation_row is None:
                request = await self.get_apply_request(result.config_apply_run_id)
                if request is None:
                    raise LookupError("config validation request was not found")
                validation_row = ConfigValidationRun(
                    config_apply_run_id=result.config_apply_run_id,
                    client_id=result.client_id,
                    desired_state_version=result.desired_state_version,
                    status=result.status,
                    errors=list(result.errors),
                    warnings=list(result.warnings),
                    requested_by_user_id=request.requested_by_user_id,
                    requested_at=request.requested_at,
                    validated_at=result.validated_at,
                )
                session.add(validation_row)
            else:
                validation_row.desired_state_version = result.desired_state_version
                validation_row.status = result.status
                validation_row.errors = list(result.errors)
                validation_row.warnings = list(result.warnings)
                validation_row.validated_at = result.validated_at
            stream_events = [
                self._stream_service.build_event(
                    event_name="client.updated",
                    client_id=validation_row.client_id,
                    incident_id=None,
                    chat_session_id=None,
                    subject_type="config_validation_run",
                    subject_id=validation_row.config_apply_run_id,
                    payload={
                        "config_apply_run_id": validation_row.config_apply_run_id,
                        "desired_state_version": validation_row.desired_state_version,
                        "status": validation_row.status,
                        "errors": list(validation_row.errors),
                        "warnings": list(validation_row.warnings),
                    },
                    occurred_at=result.validated_at,
                )
            ]
            session.add_all(stream_events)
            await self._audit_service.append_entry(
                session,
                AuditEntryInput(
                    client_id=validation_row.client_id,
                    config_apply_run_id=validation_row.config_apply_run_id,
                    actor_type="agent",
                    actor_id="config-deployer",
                    target_type="config_validation_run",
                    target_id=validation_row.config_apply_run_id,
                    action="config.validation.completed",
                    outcome=result.status,
                    details={
                        "desired_state_version": validation_row.desired_state_version,
                        "errors": list(validation_row.errors),
                        "warnings": list(validation_row.warnings),
                    },
                    occurred_at=result.validated_at,
                ),
            )
            await session.flush()
            await session.commit()
        for stream_event in stream_events:
            await self._stream_service.publish(stream_event)

    async def get_apply_request(
        self,
        config_apply_run_id: str,
    ) -> ConfigApplyRequested | None:
        async with self._database.session_factory() as session:
            apply_row = await session.get(ConfigApplyRun, config_apply_run_id)
            if apply_row is not None:
                return ConfigApplyRequested(
                    config_apply_run_id=apply_row.config_apply_run_id,
                    client_id=apply_row.client_id,
                    desired_state_version=apply_row.desired_state_version,
                    requested_by_user_id=apply_row.requested_by_user_id,
                    requested_at=apply_row.requested_at,
                )
            validation_row = await session.get(ConfigValidationRun, config_apply_run_id)
            if validation_row is None:
                return None
            return ConfigApplyRequested(
                config_apply_run_id=validation_row.config_apply_run_id,
                client_id=validation_row.client_id,
                desired_state_version=validation_row.desired_state_version,
                requested_by_user_id=validation_row.requested_by_user_id,
                requested_at=validation_row.requested_at,
            )

    async def _finish_apply(
        self,
        config_apply_run_id: str,
        *,
        target_state: ConfigApplyState,
        error_summary: str | None,
        audit_action: str,
        audit_outcome: str,
    ) -> ConfigApplyResult:
        stream_events = []
        async with self._database.session_factory() as session:
            row = await _require_apply_run(session, config_apply_run_id)
            CONFIG_APPLY_STATE_MACHINE.require_transition(
                ConfigApplyState(row.status), target_state
            )
            now = utc_now()
            row.status = target_state.value
            row.completed_at = now
            row.error_summary = error_summary
            result = _apply_result(row)
            stream_events = [
                self._build_apply_stream_event(
                    row,
                    payload=_apply_result_payload(row),
                    occurred_at=now,
                )
            ]
            session.add_all(stream_events)
            await self._audit_service.append_entry(
                session,
                AuditEntryInput(
                    client_id=row.client_id,
                    config_apply_run_id=row.config_apply_run_id,
                    approval_id=row.approval_id,
                    actor_type="agent",
                    actor_id="config-deployer",
                    target_type="config_apply_run",
                    target_id=row.config_apply_run_id,
                    action=audit_action,
                    outcome=audit_outcome,
                    details={
                        "desired_state_version": row.desired_state_version,
                        "error_summary": error_summary or "",
                    },
                    occurred_at=now,
                ),
            )
            await session.flush()
            await session.commit()
        for stream_event in stream_events:
            await self._stream_service.publish(stream_event)
        return result

    def _build_apply_stream_event(
        self,
        row: ConfigApplyRun,
        *,
        payload: dict[str, object],
        occurred_at,
    ):
        return self._stream_service.build_event(
            event_name="client.updated",
            client_id=row.client_id,
            incident_id=None,
            chat_session_id=None,
            subject_type="config_apply_run",
            subject_id=row.config_apply_run_id,
            payload=payload,
            occurred_at=occurred_at,
        )


async def _require_apply_run(
    session: AsyncSession,
    config_apply_run_id: str,
) -> ConfigApplyRun:
    row = await session.get(ConfigApplyRun, config_apply_run_id)
    if row is None:
        raise LookupError("config apply run was not found")
    return row


def _apply_run_record(row: ConfigApplyRun) -> ConfigApplyRunRecord:
    return ConfigApplyRunRecord(
        config_apply_run_id=row.config_apply_run_id,
        client_id=row.client_id,
        desired_state_version=row.desired_state_version,
        status=ConfigApplyState(row.status),
        requested_by_user_id=row.requested_by_user_id,
        requested_at=row.requested_at,
        approval_id=row.approval_id,
        started_at=row.started_at,
        completed_at=row.completed_at,
        error_summary=row.error_summary,
    )


def _apply_result(row: ConfigApplyRun) -> ConfigApplyResult:
    return ConfigApplyResult(
        config_apply_run_id=row.config_apply_run_id,
        client_id=row.client_id,
        desired_state_version=row.desired_state_version,
        status=ConfigApplyState(row.status),
        started_at=row.started_at,
        completed_at=row.completed_at,
        error_summary=row.error_summary,
    )


def _apply_result_payload(row: ConfigApplyRun) -> dict[str, object]:
    return {
        "config_apply_run_id": row.config_apply_run_id,
        "desired_state_version": row.desired_state_version,
        "status": row.status,
        "started_at": (
            None
            if row.started_at is None
            else row.started_at.isoformat().replace("+00:00", "Z")
        ),
        "completed_at": (
            None
            if row.completed_at is None
            else row.completed_at.isoformat().replace("+00:00", "Z")
        ),
        "error_summary": row.error_summary,
        "approval_id": row.approval_id,
    }


__all__ = ["ConfigRunService", "OpsPlaneConfigRunStore"]

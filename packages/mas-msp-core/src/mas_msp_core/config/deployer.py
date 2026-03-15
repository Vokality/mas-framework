"""Phase-4 desired-state validation and apply orchestration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import ValidationError

from mas_msp_contracts import (
    ApprovalRequested,
    ConfigApplyResult,
    ConfigDesiredState,
    ConfigValidationResult,
)
from mas_msp_core.agent_ids import CONFIG_DEPLOYER_AGENT_ID
from mas_msp_core.alerting import (
    AppliedAlertConfiguration,
    AppliedAlertPolicyStore,
)
from mas_msp_core.approvals import ApprovalController, ApprovalRecord

from .ports import ConfigRunStore, DesiredStateStore


APPLY_ORDER = (
    "tenant_metadata",
    "policy",
    "inventory_sources",
    "notification_routes",
)

SECRET_VALUE_KEYS = frozenset(
    {
        "api_key",
        "password",
        "secret",
        "secret_value",
        "token",
    }
)


class ConfigDeployerAgent:
    """Validate and apply desired-state config in deterministic order."""

    def __init__(
        self,
        *,
        desired_state_store: DesiredStateStore,
        run_store: ConfigRunStore,
        approval_controller: ApprovalController,
        applied_policy_store: AppliedAlertPolicyStore | None = None,
    ) -> None:
        self._desired_state_store = desired_state_store
        self._run_store = run_store
        self._approval_controller = approval_controller
        self._applied_policy_store = applied_policy_store

    async def validate_run(
        self,
        config_apply_run_id: str,
    ) -> ConfigValidationResult:
        """Validate the latest desired state for one client."""

        apply_request = await self._run_store.get_apply_request(config_apply_run_id)
        if apply_request is None:
            raise LookupError("config apply request was not found")
        desired_state = await self._require_desired_state(apply_request.client_id)
        errors, warnings = self._validate(desired_state)
        result = ConfigValidationResult(
            config_apply_run_id=config_apply_run_id,
            client_id=apply_request.client_id,
            desired_state_version=desired_state.desired_state_version,
            status="invalid" if errors else "valid",
            errors=errors,
            warnings=warnings,
            validated_at=datetime.now(UTC),
        )
        await self._run_store.record_validation_result(result)
        return result

    async def request_apply(
        self,
        config_apply_run_id: str,
    ) -> ApprovalRecord:
        """Create the approval request required before an apply run can execute."""

        apply_request = await self._run_store.get_apply_request(config_apply_run_id)
        if apply_request is None:
            raise LookupError("config apply request was not found")
        desired_state = await self._require_desired_state(apply_request.client_id)
        approval = await self._approval_controller.request_approval(
            ApprovalRequested(
                approval_id=str(uuid4()),
                client_id=apply_request.client_id,
                fabric_id=desired_state.fabric_id,
                incident_id=None,
                action_kind="config.apply",
                title=f"Apply desired state v{desired_state.desired_state_version}",
                requested_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                requested_by_agent=CONFIG_DEPLOYER_AGENT_ID,
                payload={
                    "action_scope": "config_apply",
                    "config_apply_run_id": config_apply_run_id,
                },
                risk_summary=(
                    "Applying desired state mutates client configuration in "
                    "deterministic section order."
                ),
            )
        )
        await self._run_store.attach_approval(
            config_apply_run_id,
            approval_id=approval.approval_id,
        )
        return approval

    async def execute_approved_apply(
        self,
        approval: ApprovalRecord,
    ) -> ConfigApplyResult:
        """Consume an approved apply request and execute deterministic steps."""

        config_apply_run_id = _require_config_apply_run_id(approval)
        apply_run = await self._run_store.start_validation(config_apply_run_id)
        desired_state = await self._require_desired_state(apply_run.client_id)
        errors, warnings = self._validate(desired_state)
        validation = ConfigValidationResult(
            config_apply_run_id=config_apply_run_id,
            client_id=apply_run.client_id,
            desired_state_version=desired_state.desired_state_version,
            status="invalid" if errors else "valid",
            errors=errors,
            warnings=warnings,
            validated_at=datetime.now(UTC),
        )
        await self._run_store.record_validation_result(validation)
        if errors:
            return await self._run_store.fail_apply(
                config_apply_run_id,
                error_summary=errors[0],
            )

        await self._run_store.start_apply(config_apply_run_id)
        for section_name in APPLY_ORDER:
            await self._run_store.record_apply_step(
                config_apply_run_id,
                step_name=section_name,
                outcome="applied",
                details={"keys": _keys_for_section(desired_state, section_name)},
            )
        if self._applied_policy_store is not None:
            await self._applied_policy_store.apply_configuration(
                AppliedAlertConfiguration.from_desired_state(desired_state)
            )
        return await self._run_store.complete_apply(config_apply_run_id)

    async def reject_apply(self, approval: ApprovalRecord) -> ConfigApplyResult:
        """Cancel a pending apply run after approval rejection or expiry."""

        return await self._run_store.cancel_apply(
            _require_config_apply_run_id(approval),
            reason="approval_not_granted",
        )

    async def cancel_apply(self, approval: ApprovalRecord) -> ConfigApplyResult:
        """Cancel a pending apply run before execution begins."""

        return await self._run_store.cancel_apply(
            _require_config_apply_run_id(approval),
            reason="action_withdrawn",
        )

    async def _require_desired_state(self, client_id: str) -> ConfigDesiredState:
        desired_state = await self._desired_state_store.get_desired_state(client_id)
        if desired_state is None:
            raise LookupError("desired-state config was not found")
        return desired_state

    def _validate(
        self,
        desired_state: ConfigDesiredState,
    ) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        for section_name in APPLY_ORDER:
            section_value = getattr(desired_state, section_name)
            if section_name in {"inventory_sources", "notification_routes"}:
                if not isinstance(section_value, list):
                    errors.append(f"{section_name} must be a list")
                    continue
                for index, item in enumerate(section_value):
                    if not isinstance(item, dict):
                        errors.append(f"{section_name}[{index}] must be an object")
                        continue
                    if "kind" not in item or not isinstance(item["kind"], str):
                        errors.append(f"{section_name}[{index}].kind must be a string")
            elif not isinstance(section_value, dict):
                errors.append(f"{section_name} must be an object")
            errors.extend(_find_secret_value_errors(section_name, section_value))
        errors.extend(_validate_alerting_policy(desired_state))
        if not desired_state.notification_routes:
            warnings.append("No notification routes are configured.")
        return errors, warnings


def _require_config_apply_run_id(approval: ApprovalRecord) -> str:
    run_id = approval.payload.get("config_apply_run_id")
    if not isinstance(run_id, str) or not run_id:
        raise LookupError("approval payload does not contain a config_apply_run_id")
    return run_id


def _find_secret_value_errors(
    path: str,
    value: object,
) -> list[str]:
    if isinstance(value, dict):
        errors: list[str] = []
        for key, nested in value.items():
            nested_path = f"{path}.{key}"
            if key in SECRET_VALUE_KEYS:
                errors.append(f"{nested_path} may contain only secret references.")
            errors.extend(_find_secret_value_errors(nested_path, nested))
        return errors
    if isinstance(value, list):
        errors: list[str] = []
        for index, nested in enumerate(value):
            errors.extend(_find_secret_value_errors(f"{path}[{index}]", nested))
        return errors
    return []


def _keys_for_section(
    desired_state: ConfigDesiredState, section_name: str
) -> list[str]:
    section_value = getattr(desired_state, section_name)
    if isinstance(section_value, dict):
        return sorted(section_value.keys())
    if isinstance(section_value, list):
        return [str(index) for index in range(len(section_value))]
    return []


def _validate_alerting_policy(desired_state: ConfigDesiredState) -> list[str]:
    try:
        AppliedAlertConfiguration.from_desired_state(desired_state)
    except ValidationError as exc:
        errors: list[str] = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"])
            resolved_location = (
                f"policy.alerting.{location}"
                if location.startswith(("host_defaults", "overrides", "source_alerts"))
                else f"notification_routes.{location}"
            )
            errors.append(f"{resolved_location} {error['msg']}")
        return errors
    return []


__all__ = ["APPLY_ORDER", "ConfigDeployerAgent"]

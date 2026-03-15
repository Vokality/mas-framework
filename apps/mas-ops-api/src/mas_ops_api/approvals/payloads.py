"""Structured approval payload helpers."""

from __future__ import annotations

from dataclasses import dataclass

from mas_msp_contracts import RemediationExecute


@dataclass(frozen=True, slots=True)
class IncidentRemediationApprovalPayload:
    """Deferred incident remediation payload persisted inside an approval."""

    chat_session_id: str
    turn_id: str
    incident_id: str
    asset_ids: list[str]
    requested_action: str
    remediation_execute: RemediationExecute | None = None

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, object],
    ) -> "IncidentRemediationApprovalPayload | None":
        """Build a payload model from untyped approval JSON."""

        if payload.get("action_scope") != "incident_remediation":
            return None
        chat_session_id = payload.get("chat_session_id")
        turn_id = payload.get("turn_id")
        incident_id = payload.get("incident_id")
        requested_action = payload.get("requested_action")
        remediation_payload = payload.get("remediation_execute")
        asset_ids = payload.get("asset_ids")
        if (
            not isinstance(chat_session_id, str)
            or not isinstance(turn_id, str)
            or not isinstance(incident_id, str)
            or not isinstance(requested_action, str)
        ):
            return None
        resolved_asset_ids: list[str] = []
        if isinstance(asset_ids, list) and all(
            isinstance(asset_id, str) for asset_id in asset_ids
        ):
            resolved_asset_ids = [
                asset_id for asset_id in asset_ids if isinstance(asset_id, str)
            ]
        remediation_execute = (
            RemediationExecute.model_validate(remediation_payload)
            if isinstance(remediation_payload, dict)
            else None
        )
        return cls(
            chat_session_id=chat_session_id,
            turn_id=turn_id,
            incident_id=incident_id,
            asset_ids=resolved_asset_ids,
            requested_action=requested_action,
            remediation_execute=remediation_execute,
        )


__all__ = ["IncidentRemediationApprovalPayload"]

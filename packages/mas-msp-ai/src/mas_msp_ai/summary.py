"""Incident summary drafting for the Phase 3 cockpit."""

from __future__ import annotations

from typing import cast

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from .deps import SummaryComposerDeps
from .outputs import SummaryDraft


class SummaryComposer:
    """Draft operator-facing summaries from structured incident context."""

    async def compose(self, deps: SummaryComposerDeps) -> SummaryDraft:
        """Return a validated summary draft for the current incident state."""

        payload = SummaryDraft(
            incident_id=deps.incident_id,
            headline=self._headline(deps),
            operator_summary=self._summary(deps),
            affected_assets=self._affected_assets(deps),
            recommended_actions=self._recommended_actions(deps),
        )
        agent = Agent(
            TestModel(custom_output_args=payload.model_dump(mode="json")),
            deps_type=SummaryComposerDeps,
            output_type=SummaryDraft,
        )
        result = await agent.run(
            "Compose an operator summary for the incident.", deps=deps
        )
        return cast(SummaryDraft, result.output)

    def _headline(self, deps: SummaryComposerDeps) -> str:
        if deps.recent_evidence:
            return deps.recent_evidence[-1].summary
        return deps.incident_record.summary

    def _summary(self, deps: SummaryComposerDeps) -> str:
        asset_list = ", ".join(self._affected_assets(deps)) or "unknown assets"
        evidence_lines = [
            f"- {bundle.summary}" for bundle in deps.recent_evidence[-3:]
        ] or ["- No recent evidence has been collected yet."]
        return "\n".join(
            [
                f"Incident `{deps.incident_id}` is currently `{deps.incident_record.state.value}`.",
                f"Affected assets: {asset_list}.",
                "",
                "Key evidence:",
                *evidence_lines,
            ]
        )

    def _affected_assets(self, deps: SummaryComposerDeps) -> list[str]:
        labels = [
            asset.hostname or asset.mgmt_address or asset.asset_id
            for asset in deps.asset_refs
        ]
        return labels or [deps.incident_record.incident_id]

    def _recommended_actions(
        self, deps: SummaryComposerDeps
    ) -> list[dict[str, object]]:
        if deps.recent_evidence:
            return [
                {
                    "action_kind": "network.review",
                    "title": "Review the latest diagnostic evidence",
                    "details": deps.recent_evidence[-1].summary,
                },
                {
                    "action_kind": "network.monitor",
                    "title": "Keep the affected asset under watch",
                    "details": "Continue monitoring read-only health and alert activity.",
                },
            ]
        return [
            {
                "action_kind": "network.observe",
                "title": "Collect read-only diagnostics",
                "details": "Gather additional evidence before recommending remediation.",
            }
        ]


__all__ = ["SummaryComposer"]

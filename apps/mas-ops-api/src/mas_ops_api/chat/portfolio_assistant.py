"""Global portfolio chat backed only by ops-plane read models."""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from sqlalchemy.ext.asyncio import AsyncSession

from mas_ops_api.projections.repository import PortfolioQueries


class PortfolioAssistantReply(BaseModel):
    """Validated reply returned to the global chat panel."""

    markdown_summary: str


class PortfolioAssistant:
    """Summarize authorized portfolio state without fabric-local access."""

    async def respond(
        self,
        session: AsyncSession,
        *,
        allowed_client_ids: frozenset[str],
        admin: bool,
        message: str,
    ) -> str:
        """Return an operator-facing summary for the current portfolio view."""

        clients = await PortfolioQueries.list_clients(
            session,
            allowed_client_ids=allowed_client_ids,
            admin=admin,
        )
        incident_lines: list[str] = []
        for client in clients[:3]:
            incidents = await PortfolioQueries.list_incidents_for_client(
                session, client.client_id
            )
            for incident in incidents[:2]:
                incident_lines.append(
                    f"- {incident.summary} ({incident.severity}, {client.name})"
                )
        payload = PortfolioAssistantReply(
            markdown_summary="\n".join(
                [
                    f"You asked: {message}",
                    "",
                    f"Authorized clients: {len(clients)}",
                    *[
                        f"- {client.name}: {client.open_alert_count} open alerts, {client.critical_asset_count} critical assets"
                        for client in clients
                    ],
                    "",
                    "Notable incidents:",
                    *(
                        incident_lines
                        or ["- No notable incidents are currently projected."]
                    ),
                ]
            )
        )
        agent = Agent(
            TestModel(custom_output_args=payload.model_dump(mode="json")),
            output_type=PortfolioAssistantReply,
        )
        result = await agent.run("Summarize the authorized portfolio for the operator.")
        reply = cast(PortfolioAssistantReply, result.output)
        return reply.markdown_summary


__all__ = ["PortfolioAssistant"]

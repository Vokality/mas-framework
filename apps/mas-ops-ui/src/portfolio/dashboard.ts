import type { ClientSummaryResponse } from "../api/client";

export type ClientAttentionLevel = "critical" | "elevated" | "stable";

export function compareClientsByAttention(
  left: ClientSummaryResponse,
  right: ClientSummaryResponse,
): number {
  return rankClient(right) - rankClient(left);
}

export function getClientAttentionLevel(
  client: ClientSummaryResponse,
): ClientAttentionLevel {
  if (client.critical_asset_count > 0 || client.open_alert_count >= 3) {
    return "critical";
  }
  if (client.open_alert_count > 0) {
    return "elevated";
  }
  return "stable";
}

export function getClientAttentionLabel(
  client: ClientSummaryResponse,
): string {
  switch (getClientAttentionLevel(client)) {
    case "critical":
      return "Needs incident review";
    case "elevated":
      return "Watch closely";
    case "stable":
      return "Stable";
  }
}

export function getClientNextStep(client: ClientSummaryResponse): string {
  if (client.critical_asset_count > 0) {
    return "Open the client workspace, inspect active incidents, and use the incident cockpit before taking write actions.";
  }
  if (client.open_alert_count > 0) {
    return "Review the client workspace for the freshest incident and decide whether diagnostics or escalation are needed.";
  }
  return "Use this client as a reference point, then move to the next client that has active alerts.";
}

export function getClientNextActionLabel(
  client: ClientSummaryResponse,
): string {
  if (client.critical_asset_count > 0) {
    return "Open incident workspace";
  }
  if (client.open_alert_count > 0) {
    return "Review alerts and incidents";
  }
  return "Monitor only";
}

function rankClient(client: ClientSummaryResponse): number {
  return client.critical_asset_count * 100 + client.open_alert_count * 10;
}

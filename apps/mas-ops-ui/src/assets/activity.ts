import type { ActivityEventResponse } from "../api/client";

type JsonRecord = Record<string, unknown>;

export function describeActivityEvent(event: ActivityEventResponse): string {
  if (event.event_type === "network.alert.raised") {
    return extractAlertTitle(event) ?? "Network alert raised";
  }
  if (event.event_type === "asset.health.changed") {
    const health = extractSnapshotHealth(event);
    return health ? `Health changed to ${health.toUpperCase()}` : "Asset health changed";
  }
  if (event.event_type === "network.snapshot.recorded") {
    const health = extractSnapshotHealth(event);
    return health ? `Health snapshot recorded: ${health.toUpperCase()}` : "Health snapshot recorded";
  }
  if (event.event_type === "asset.upserted") {
    const hostname = extractAssetHostname(event);
    return hostname ? `Asset synchronized: ${hostname}` : "Asset synchronized";
  }
  return event.event_type;
}

export function describeActivityMeta(event: ActivityEventResponse): string {
  if (event.event_type === "network.alert.raised") {
    const severity = extractAlertSeverity(event);
    const parts = [severity?.toUpperCase() ?? null, formatTimestamp(event.occurred_at)];
    return parts.filter(Boolean).join(" | ");
  }
  const hostname = extractAssetHostname(event);
  const parts = [hostname, formatTimestamp(event.occurred_at)];
  return parts.filter(Boolean).join(" | ");
}

export function extractAlertTitle(event: ActivityEventResponse): string | null {
  const alert = getRecord(event.payload.alert);
  const title = alert?.title;
  return typeof title === "string" && title.length > 0 ? title : null;
}

export function extractAlertSeverity(event: ActivityEventResponse): string | null {
  const alert = getRecord(event.payload.alert);
  const severity = alert?.severity;
  return typeof severity === "string" && severity.length > 0 ? severity : null;
}

export function extractAssetHostname(event: ActivityEventResponse): string | null {
  const asset =
    getRecord(event.payload.asset) ??
    getRecord(getRecord(event.payload.alert)?.asset) ??
    getRecord(getRecord(event.payload.snapshot)?.asset);
  const hostname = asset?.hostname;
  return typeof hostname === "string" && hostname.length > 0 ? hostname : null;
}

export function extractSnapshotHealth(event: ActivityEventResponse): string | null {
  const snapshot = getRecord(event.payload.snapshot);
  const healthState = snapshot?.health_state;
  return typeof healthState === "string" && healthState.length > 0
    ? healthState
    : null;
}

export function findLatestAlert(
  events: Array<ActivityEventResponse>,
): ActivityEventResponse | null {
  return events.find((event) => event.event_type === "network.alert.raised") ?? null;
}

export function findLatestSnapshot(
  events: Array<ActivityEventResponse>,
): ActivityEventResponse | null {
  return (
    events.find(
      (event) =>
        event.event_type === "network.snapshot.recorded" ||
        event.event_type === "asset.health.changed",
    ) ?? null
  );
}

export function formatTimestamp(value: string | null | undefined): string {
  if (!value) {
    return "unknown";
  }
  return value.replace("T", " ").replace("Z", " UTC");
}

function getRecord(value: unknown): JsonRecord | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as JsonRecord;
}

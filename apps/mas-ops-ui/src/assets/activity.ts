import type { ActivityEventResponse } from "../api/client";

type JsonRecord = Record<string, unknown>;
type HostMetrics = {
  cpuPercent: number | null;
  memoryPercent: number | null;
  diskPercent: number | null;
};

export function describeActivityEvent(event: ActivityEventResponse): string {
  if (event.event_type === "network.alert.raised" || event.event_type === "host.alert.raised") {
    return extractAlertTitle(event) ?? "Network alert raised";
  }
  if (event.event_type === "asset.health.changed") {
    const health = extractSnapshotHealth(event);
    return health ? `Health changed to ${health.toUpperCase()}` : "Asset health changed";
  }
  if (event.event_type === "network.snapshot.recorded" || event.event_type === "host.snapshot.recorded") {
    const health = extractSnapshotHealth(event);
    return health ? `Health snapshot recorded: ${health.toUpperCase()}` : "Health snapshot recorded";
  }
  if (event.event_type === "remediation.started") {
    const serviceName = extractServiceName(event);
    return serviceName ? `Remediation started for ${serviceName}` : "Remediation started";
  }
  if (event.event_type === "remediation.executed") {
    const serviceName = extractServiceName(event);
    const serviceState = extractServiceState(event);
    if (serviceName && serviceState) {
      return `Remediation executed: ${serviceName} is ${serviceState}`;
    }
    return serviceName ? `Remediation executed for ${serviceName}` : "Remediation executed";
  }
  if (event.event_type === "remediation.verified") {
    return "Verification diagnostics completed";
  }
  if (event.event_type === "asset.upserted") {
    const hostname = extractAssetHostname(event);
    return hostname ? `Asset synchronized: ${hostname}` : "Asset synchronized";
  }
  return event.event_type;
}

export function describeActivityMeta(event: ActivityEventResponse): string {
  if (event.event_type === "network.alert.raised" || event.event_type === "host.alert.raised") {
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
  return (
    events.find(
      (event) =>
        event.event_type === "network.alert.raised" ||
        event.event_type === "host.alert.raised",
    ) ?? null
  );
}

export function findLatestSnapshot(
  events: Array<ActivityEventResponse>,
): ActivityEventResponse | null {
  return (
    events.find(
      (event) =>
        event.event_type === "network.snapshot.recorded" ||
        event.event_type === "host.snapshot.recorded" ||
        event.event_type === "asset.health.changed",
    ) ?? null
  );
}

export function extractServiceName(event: ActivityEventResponse): string | null {
  const postState = getRecord(event.payload.post_state);
  const postStateName = postState?.service_name;
  if (typeof postStateName === "string" && postStateName.length > 0) {
    return postStateName;
  }
  const payloadName = event.payload.service_name;
  if (typeof payloadName === "string" && payloadName.length > 0) {
    return payloadName;
  }
  const normalizedFacts = getRecord(getRecord(event.payload.alert)?.normalized_facts);
  const alertName = normalizedFacts?.service_name;
  return typeof alertName === "string" && alertName.length > 0 ? alertName : null;
}

export function extractServiceState(event: ActivityEventResponse): string | null {
  const postState = getRecord(event.payload.post_state);
  const state = postState?.service_state;
  return typeof state === "string" && state.length > 0 ? state : null;
}

export function extractHostServices(
  event: ActivityEventResponse,
): Array<{ serviceName: string; serviceState: string }> {
  const services = getServicesValue(event);
  if (services === null) {
    return [];
  }
  return services
    .map((entry) => {
      const record = getRecord(entry);
      const serviceName = record?.service_name;
      const serviceState = record?.service_state;
      if (typeof serviceName !== "string" || typeof serviceState !== "string") {
        return null;
      }
      return { serviceName, serviceState };
    })
    .filter((entry): entry is { serviceName: string; serviceState: string } => entry !== null);
}

export function extractHostMetrics(event: ActivityEventResponse): HostMetrics | null {
  const metrics = getSnapshotMetrics(event);
  const hostMetrics = {
    cpuPercent: getNumericMetric(metrics?.cpu_percent),
    memoryPercent: getNumericMetric(metrics?.memory_percent),
    diskPercent: getNumericMetric(metrics?.disk_percent),
  };
  if (
    hostMetrics.cpuPercent === null &&
    hostMetrics.memoryPercent === null &&
    hostMetrics.diskPercent === null
  ) {
    return null;
  }
  return hostMetrics;
}

export function formatPercent(value: number | null): string {
  if (value === null) {
    return "unknown";
  }
  return `${Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1)}%`;
}

function getServicesValue(event: ActivityEventResponse): Array<unknown> | null {
  const metrics = getSnapshotMetrics(event);
  const metricServices = metrics?.services;
  if (Array.isArray(metricServices)) {
    return metricServices;
  }
  const directServices = event.payload.services;
  return Array.isArray(directServices) ? directServices : null;
}

function getSnapshotMetrics(event: ActivityEventResponse): JsonRecord | null {
  const snapshot = getRecord(event.payload.snapshot);
  return getRecord(snapshot?.metrics);
}

function getNumericMetric(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const parsed = Number.parseFloat(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
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

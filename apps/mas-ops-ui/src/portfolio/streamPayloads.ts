import type {
  ActivityEventResponse,
  AssetResponse,
  ClientSummaryResponse,
} from "../api/client";

export function parseClientSummaryPayload(
  payload: Record<string, unknown>,
): ClientSummaryResponse | null {
  const clientId = readString(payload, "client_id");
  const fabricId = readString(payload, "fabric_id");
  const name = readString(payload, "name");
  const openAlertCount = readInteger(payload, "open_alert_count");
  const criticalAssetCount = readInteger(payload, "critical_asset_count");
  const updatedAt = readString(payload, "updated_at");
  if (
    clientId === null ||
    fabricId === null ||
    name === null ||
    openAlertCount === null ||
    criticalAssetCount === null ||
    updatedAt === null
  ) {
    return null;
  }
  return {
    client_id: clientId,
    critical_asset_count: criticalAssetCount,
    fabric_id: fabricId,
    name,
    open_alert_count: openAlertCount,
    updated_at: updatedAt,
  };
}

export function parseAssetPayload(
  payload: Record<string, unknown>,
): AssetResponse | null {
  const assetId = readString(payload, "asset_id");
  const assetKind = readString(payload, "asset_kind");
  const clientId = readString(payload, "client_id");
  const fabricId = readString(payload, "fabric_id");
  const healthState = readString(payload, "health_state");
  const updatedAt = readString(payload, "updated_at");
  const tags = readStringArray(payload, "tags");
  if (
    assetId === null ||
    assetKind === null ||
    clientId === null ||
    fabricId === null ||
    healthState === null ||
    updatedAt === null ||
    tags === null
  ) {
    return null;
  }
  return {
    asset_id: assetId,
    asset_kind: assetKind,
    client_id: clientId,
    fabric_id: fabricId,
    health_observed_at: readOptionalString(payload, "health_observed_at"),
    health_state: healthState,
    hostname: readOptionalString(payload, "hostname"),
    last_alert_at: readOptionalString(payload, "last_alert_at"),
    mgmt_address: readOptionalString(payload, "mgmt_address"),
    model: readOptionalString(payload, "model"),
    site: readOptionalString(payload, "site"),
    tags,
    updated_at: updatedAt,
    vendor: readOptionalString(payload, "vendor"),
  };
}

export function parseActivityEventPayload(
  payload: Record<string, unknown>,
): ActivityEventResponse | null {
  const activityId = readInteger(payload, "activity_id");
  const sourceEventId = readString(payload, "source_event_id");
  const clientId = readString(payload, "client_id");
  const fabricId = readString(payload, "fabric_id");
  const eventType = readString(payload, "event_type");
  const subjectType = readString(payload, "subject_type");
  const subjectId = readString(payload, "subject_id");
  const occurredAt = readString(payload, "occurred_at");
  const nestedPayload = readRecord(payload, "payload");
  if (
    activityId === null ||
    sourceEventId === null ||
    clientId === null ||
    fabricId === null ||
    eventType === null ||
    subjectType === null ||
    subjectId === null ||
    occurredAt === null ||
    nestedPayload === null
  ) {
    return null;
  }
  return {
    activity_id: activityId,
    asset_id: readOptionalString(payload, "asset_id"),
    client_id: clientId,
    event_type: eventType,
    fabric_id: fabricId,
    incident_id: readOptionalString(payload, "incident_id"),
    occurred_at: occurredAt,
    payload: nestedPayload,
    source_event_id: sourceEventId,
    subject_id: subjectId,
    subject_type: subjectType,
  };
}

function readInteger(
  payload: Record<string, unknown>,
  key: string,
): number | null {
  const value = payload[key];
  return typeof value === "number" && Number.isInteger(value) ? value : null;
}

function readOptionalString(
  payload: Record<string, unknown>,
  key: string,
): string | null {
  const value = payload[key];
  if (value === null) {
    return null;
  }
  return typeof value === "string" ? value : null;
}

function readRecord(
  payload: Record<string, unknown>,
  key: string,
): Record<string, unknown> | null {
  const value = payload[key];
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function readString(
  payload: Record<string, unknown>,
  key: string,
): string | null {
  const value = payload[key];
  return typeof value === "string" ? value : null;
}

function readStringArray(
  payload: Record<string, unknown>,
  key: string,
): Array<string> | null {
  const value = payload[key];
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    return null;
  }
  return [...value];
}

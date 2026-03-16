import type { ActivityEventResponse, AssetResponse } from "../api/client";
import {
  describeActivityEvent,
  describeActivityMeta,
  extractAlertTitle,
  extractHostMetrics,
  extractHostServices,
  extractServiceName,
  extractServiceState,
  extractSnapshotHealth,
  findLatestAlert,
  findLatestSnapshot,
  formatPercent,
  formatTimestamp,
} from "./activity";

type AssetDetailCardProps = {
  activity: Array<ActivityEventResponse>;
  asset: AssetResponse | null;
};

export function AssetDetailCard({ activity, asset }: AssetDetailCardProps) {
  if (asset === null) {
    return (
      <article className="card">
        <h3>Asset Detail</h3>
        <p className="muted-copy">Select an asset to load the detail view.</p>
      </article>
    );
  }

  const latestAlert = findLatestAlert(activity);
  const latestSnapshot = findLatestSnapshot(activity);
  const isHostAsset = asset.asset_kind === "linux_host" || asset.asset_kind === "windows_host";
  const hostMetrics = latestSnapshot ? extractHostMetrics(latestSnapshot) : null;
  const hostServices = latestSnapshot ? extractHostServices(latestSnapshot) : [];
  const remediationHistory = activity.filter((event) => event.event_type.startsWith("remediation."));

  return (
    <article className="card">
      <div className="asset-detail-header">
        <h3>{asset.hostname ?? asset.asset_id}</h3>
        <span className={`status-pill status-pill-${asset.health_state === "critical" ? "critical" : asset.health_state === "elevated" ? "elevated" : "stable"}`}>
          {asset.health_state}
        </span>
      </div>

      {isHostAsset && hostMetrics ? (
        <div className="card-stat-grid">
          <div>
            <span>CPU</span>
            <strong>{formatPercent(hostMetrics.cpuPercent)}</strong>
          </div>
          <div>
            <span>Memory</span>
            <strong>{formatPercent(hostMetrics.memoryPercent)}</strong>
          </div>
          <div>
            <span>Disk</span>
            <strong>{formatPercent(hostMetrics.diskPercent)}</strong>
          </div>
        </div>
      ) : isHostAsset ? (
        <p className="muted-copy">No host metrics available yet.</p>
      ) : null}

      {latestAlert ? (
        <p className="muted-copy alert-hint">
          Alert: {extractAlertTitle(latestAlert) ?? "Visibility alert raised"}
        </p>
      ) : null}

      {isHostAsset && hostServices.length > 0 ? (
        <div className="list">
          {hostServices.map((service) => (
            <div className="list-item" key={`${service.serviceName}-${service.serviceState}`}>
              <span className="activity-label">{service.serviceName}</span>
              <span>{service.serviceState}</span>
            </div>
          ))}
        </div>
      ) : null}

      <p className="muted-copy">
        {asset.mgmt_address ?? asset.hostname ?? asset.asset_id}
        {asset.site ? ` · ${asset.site}` : ""}
        {` · ${asset.asset_kind}`}
      </p>
      <p className="muted-copy">
        Last health: {formatTimestamp(asset.health_observed_at)}
        {asset.last_alert_at ? ` · Last alert: ${formatTimestamp(asset.last_alert_at)}` : ""}
      </p>
      {latestSnapshot ? (
        <p className="muted-copy">Snapshot: {extractSnapshotHealth(latestSnapshot) ?? "unknown"}</p>
      ) : null}

      {remediationHistory.length > 0 ? (
        <p className="muted-copy">
          Last remediation: {extractServiceName(remediationHistory[0]) ?? "unknown service"}
          {extractServiceState(remediationHistory[0])
            ? ` → ${extractServiceState(remediationHistory[0])}`
            : ""}
        </p>
      ) : null}

      {activity.length > 0 ? (
        <div className="list">
          {activity.slice(0, 3).map((event) => (
            <div className="list-item" key={event.activity_id}>
              <span className="activity-label">{describeActivityEvent(event)}</span>
              <span>{describeActivityMeta(event)}</span>
            </div>
          ))}
        </div>
      ) : null}
    </article>
  );
}

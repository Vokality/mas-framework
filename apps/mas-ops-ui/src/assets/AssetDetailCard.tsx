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
  const latestRemediation = remediationHistory[0] ?? null;

  return (
    <article className="card">
      <h3>Asset Detail</h3>
      <p className="muted-copy">Asset ID: {asset.asset_id}</p>
      <p className="muted-copy">
        Vendor: {asset.vendor ?? "unknown"}
        <br />
        Model: {asset.model ?? "unknown"}
        <br />
        Site: {asset.site ?? "unknown"}
      </p>
      <p className="muted-copy">
        Hostname: {asset.hostname ?? "unknown"}
        <br />
        Management: {asset.mgmt_address ?? "unknown"}
      </p>
      <p className="muted-copy">
        Health: {asset.health_state}
        <br />
        Last health: {formatTimestamp(asset.health_observed_at)}
        <br />
        Last alert: {formatTimestamp(asset.last_alert_at)}
      </p>
      {latestSnapshot ? (
        <p className="muted-copy">
          Latest snapshot: {extractSnapshotHealth(latestSnapshot) ?? "unknown"}
        </p>
      ) : null}
      {latestAlert ? (
        <p className="muted-copy">
          Latest alert: {extractAlertTitle(latestAlert) ?? "Visibility alert raised"}
        </p>
      ) : null}
      <p className="muted-copy">Tags: {asset.tags.join(", ") || "none"}</p>
      {isHostAsset ? (
        <>
          <p className="muted-copy">
            Platform: {asset.asset_kind === "linux_host" ? "Linux" : "Windows"}
          </p>
          {hostMetrics ? (
            <>
              <h4>Latest host metrics</h4>
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
            </>
          ) : (
            <p className="muted-copy">No host metrics are available yet.</p>
          )}
          {hostServices.length > 0 ? (
            <div className="list">
              {hostServices.map((service) => (
                <div className="list-item" key={`${service.serviceName}-${service.serviceState}`}>
                  <strong>{service.serviceName}</strong>
                  <span>{service.serviceState}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="muted-copy">No host service snapshot is available yet.</p>
          )}
          {latestRemediation ? (
            <p className="muted-copy">
              Latest remediation: {extractServiceName(latestRemediation) ?? "unknown service"}
              {extractServiceState(latestRemediation)
                ? ` is ${extractServiceState(latestRemediation)}`
                : ""}
            </p>
          ) : null}
        </>
      ) : null}
      <div className="list">
        {activity.slice(0, 3).map((event) => (
          <div className="list-item" key={event.activity_id}>
            <strong>{describeActivityEvent(event)}</strong>
            <span>{describeActivityMeta(event)}</span>
          </div>
        ))}
      </div>
      {isHostAsset && remediationHistory.length > 0 ? (
        <div className="list">
          {remediationHistory.slice(0, 3).map((event) => (
            <div className="list-item" key={`remediation-${event.activity_id}`}>
              <strong>{describeActivityEvent(event)}</strong>
              <span>{describeActivityMeta(event)}</span>
            </div>
          ))}
        </div>
      ) : null}
    </article>
  );
}

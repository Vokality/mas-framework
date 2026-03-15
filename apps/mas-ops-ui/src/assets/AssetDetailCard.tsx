import type { ActivityEventResponse, AssetResponse } from "../api/client";
import {
  describeActivityEvent,
  describeActivityMeta,
  extractAlertTitle,
  extractSnapshotHealth,
  findLatestAlert,
  findLatestSnapshot,
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
          Latest alert: {extractAlertTitle(latestAlert) ?? "Network alert raised"}
        </p>
      ) : null}
      <p className="muted-copy">Tags: {asset.tags.join(", ") || "none"}</p>
      <div className="list">
        {activity.slice(0, 3).map((event) => (
          <div className="list-item" key={event.activity_id}>
            <strong>{describeActivityEvent(event)}</strong>
            <span>{describeActivityMeta(event)}</span>
          </div>
        ))}
      </div>
    </article>
  );
}

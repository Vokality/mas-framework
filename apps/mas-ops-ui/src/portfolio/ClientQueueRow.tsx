import { Link } from "react-router-dom";

import type { ClientSummaryResponse } from "../api/client";
import {
  getClientAttentionLabel,
  getClientAttentionLevel,
  getClientNextActionLabel,
} from "./dashboard";

type ClientQueueRowProps = {
  client: ClientSummaryResponse;
  isPriorityClient: boolean;
};

export function ClientQueueRow({
  client,
  isPriorityClient,
}: ClientQueueRowProps) {
  const attentionLevel = getClientAttentionLevel(client);

  return (
    <article className={`queue-row queue-row-${attentionLevel}`}>
      <div className="queue-row-cell queue-row-client" data-label="Client">
        <div className="queue-row-client-title">
          <strong>{client.name}</strong>
          {isPriorityClient ? <span className="priority-chip">Next up</span> : null}
        </div>
        <div className="queue-row-meta">
          <span className={`status-pill status-pill-${attentionLevel}`}>
            {getClientAttentionLabel(client)}
          </span>
          <span className="mono-copy">{client.client_id}</span>
        </div>
      </div>
      <div className="queue-row-cell queue-row-metric" data-label="Alerts">
        <strong>{client.open_alert_count}</strong>
        <span>Alerts</span>
      </div>
      <div className="queue-row-cell queue-row-metric" data-label="Critical">
        <strong>{client.critical_asset_count}</strong>
        <span>Critical</span>
      </div>
      <div className="queue-row-cell queue-row-fabric" data-label="Fabric">
        <span className="mono-copy">{client.fabric_id}</span>
      </div>
      <div className="queue-row-cell queue-row-next-step" data-label="Next action">
        <strong>{getClientNextActionLabel(client)}</strong>
      </div>
      <div className="queue-row-cell queue-row-actions" data-label="Actions">
        <Link to={`/clients/${client.client_id}`}>Open</Link>
        <Link className="secondary-link" to={`/clients/${client.client_id}/config`}>
          Config
        </Link>
      </div>
    </article>
  );
}

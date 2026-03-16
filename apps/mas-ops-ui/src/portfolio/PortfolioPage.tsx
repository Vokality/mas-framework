import { useEffect, useReducer } from "react";
import { Link } from "react-router-dom";

import {
  ApiError,
  masOpsApiClient,
  type ClientSummaryResponse,
} from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { useManagedStream } from "../streams";
import { ClientQueueRow } from "./ClientQueueRow";
import {
  compareClientsByAttention,
  getClientNextActionLabel,
} from "./dashboard";
import {
  initialPortfolioState,
  portfolioReducer,
} from "./reducer";
import { parseClientSummaryPayload } from "./streamPayloads";

export function PortfolioPage() {
  const auth = useAuth();
  const [state, dispatch] = useReducer(
    portfolioReducer,
    initialPortfolioState,
  );

  async function loadClients() {
    try {
      const clients = await masOpsApiClient.listClients();
      dispatch({ type: "loaded", clients });
    } catch (error) {
      console.error("Failed to load portfolio clients", error);
      dispatch({ type: "failed", message: getPortfolioErrorMessage(error) });
    }
  }

  useEffect(() => {
    dispatch({ type: "loading" });
    void loadClients();
  }, []);

  const streamState = useManagedStream({
    enabled: auth.isAuthenticated,
    eventTypes: ["portfolio.updated", "client.updated"],
    liveUrl: masOpsApiClient.getPortfolioStreamUrl(),
    replay(lastEventId) {
      return masOpsApiClient.fetchPortfolioStream({
        lastEventId,
        replayOnly: true,
      });
    },
    scopeKey: `portfolio:${auth.session?.user_id ?? "anonymous"}`,
    onEvent(frame) {
      if (frame.event === "portfolio.updated") {
        void loadClients();
        return;
      }
      const client = parseClientSummaryPayload(frame.data.payload);
      if (client !== null) {
        dispatch({ type: "client_replaced", client });
        return;
      }
      if (frame.data.client_id === null) {
        return;
      }
      void refreshClient(frame.data.client_id, dispatch);
    },
    onForbidden() {
      dispatch({
        type: "failed",
        message: "Portfolio stream access was revoked during reconnect.",
      });
    },
  });

  const totalAlerts = state.clients.reduce(
    (sum, client) => sum + client.open_alert_count,
    0,
  );
  const totalCriticalAssets = state.clients.reduce(
    (sum, client) => sum + client.critical_asset_count,
    0,
  );
  const orderedClients = [...state.clients].sort(compareClientsByAttention);
  const priorityClient = orderedClients[0] ?? null;
  const clientsNeedingAttention = orderedClients.filter(
    (client) => client.open_alert_count > 0 || client.critical_asset_count > 0,
  ).length;
  const stableClients = state.clients.length - clientsNeedingAttention;

  return (
    <div className="page-stack">
      <section className="portfolio-toolbar card">
        <div className="portfolio-toolbar-copy">
          <span className="eyebrow">Portfolio Queue</span>
          <h2>Client triage</h2>
          <p className="hero-subtitle">
            Use the queue to decide which client to open next. The operational
            path is queue, then client workspace, then incident cockpit.
          </p>
        </div>
        <div className="portfolio-toolbar-status">
          <div className="portfolio-status-pill">
            <span className="portfolio-status-label">Next up</span>
            <strong>{priorityClient?.name ?? "No active client"}</strong>
            <span className="muted-copy">
              {priorityClient
                ? getClientNextActionLabel(priorityClient)
                : "Waiting for projected client data"}
            </span>
          </div>
          <p className="muted-copy">Stream: {streamState}</p>
          {state.status === "ready" && state.errorMessage ? (
            <p className="form-error">{state.errorMessage}</p>
          ) : null}
        </div>
        <div className="card-actions portfolio-toolbar-actions">
          {priorityClient ? (
            <Link to={`/clients/${priorityClient.client_id}`}>
              Open Priority Client
            </Link>
          ) : null}
          <Link className="secondary-link" to="/approvals">
            Approvals
          </Link>
          <Link className="secondary-link" to="/chat">
            Portfolio Chat
          </Link>
        </div>
      </section>
      <section className="portfolio-summary-strip">
        <article className="summary-tile">
          <span>Clients</span>
          <strong>{state.clients.length}</strong>
        </article>
        <article className="summary-tile">
          <span>Need attention</span>
          <strong>{clientsNeedingAttention}</strong>
        </article>
        <article className="summary-tile">
          <span>Alerts</span>
          <strong>{totalAlerts}</strong>
        </article>
        <article className="summary-tile">
          <span>Critical assets</span>
          <strong>{totalCriticalAssets}</strong>
        </article>
        <article className="summary-tile">
          <span>Stable clients</span>
          <strong>{stableClients}</strong>
        </article>
      </section>
      <section className="queue-table-card card">
        <div className="queue-table-toolbar">
          <div>
            <span className="eyebrow">Client Queue</span>
            <h3>Dense triage view</h3>
          </div>
          <p className="muted-copy">
            Sort order is automatic: clients with critical assets and more alerts
            rise to the top.
          </p>
        </div>
        <div className="queue-table-head">
          <span>Client</span>
          <span>Alerts</span>
          <span>Critical</span>
          <span>Fabric</span>
          <span>Next Action</span>
          <span>Actions</span>
        </div>
        <div className="queue-table-body">
          {state.status === "loading" ? (
            <article className="queue-empty-state">
              <strong>Loading portfolio</strong>
              <span>Fetching authorized client summaries from the ops API.</span>
            </article>
          ) : null}
          {state.status === "error" ? (
            <article className="queue-empty-state">
              <strong>Portfolio unavailable</strong>
              <span>{state.errorMessage}</span>
            </article>
          ) : null}
          {state.status === "ready" && state.clients.length === 0 ? (
            <article className="queue-empty-state">
              <strong>No visible clients yet</strong>
              <span>
                No client grants are currently available for this session, so the
                queue is empty.
              </span>
            </article>
          ) : null}
          {state.status === "ready"
            ? orderedClients.map((client, index) => (
                <ClientQueueRow
                  client={client}
                  isPriorityClient={index === 0}
                  key={client.client_id}
                />
              ))
            : null}
        </div>
      </section>
    </div>
  );
}

async function refreshClient(
  clientId: string,
  dispatch: (action: {
    type: "client_replaced";
    client: ClientSummaryResponse;
  }) => void,
) {
  try {
    const client = await masOpsApiClient.getClient(clientId);
    dispatch({ type: "client_replaced", client });
  } catch (error) {
    if (error instanceof ApiError && (error.status === 403 || error.status === 404)) {
      return;
    }
    console.error("Failed to refresh streamed client summary", error);
  }
}

function getPortfolioErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 403) {
    return "The current session cannot read portfolio data.";
  }
  return "The ops API did not return the portfolio view.";
}

import { useEffect, useReducer } from "react";
import { Link } from "react-router-dom";

import {
  ApiError,
  masOpsApiClient,
  type ClientSummaryResponse,
} from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { useManagedStream } from "../streams";
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

  return (
    <>
      <section className="hero">
        <span className="eyebrow">Portfolio</span>
        <h2>Authorized client summaries</h2>
        <p>
          This dashboard is backed by `GET /clients` and the portfolio SSE stream.
        </p>
        <p className="muted-copy">Stream: {streamState}</p>
        {state.status === "ready" && state.errorMessage ? (
          <p className="form-error">{state.errorMessage}</p>
        ) : null}
      </section>
      <section className="grid">
        <article className="card">
          <h3>Authorized Clients</h3>
          <p className="metric-copy">{state.clients.length}</p>
        </article>
        <article className="card">
          <h3>Open Alerts</h3>
          <p className="metric-copy">{totalAlerts}</p>
        </article>
        <article className="card">
          <h3>Critical Assets</h3>
          <p className="metric-copy">{totalCriticalAssets}</p>
        </article>
      </section>
      <section className="grid">
        {state.status === "loading" ? (
          <article className="card">
            <h3>Loading portfolio</h3>
            <p>Fetching authorized client summaries from the ops API.</p>
          </article>
        ) : null}
        {state.status === "error" ? (
          <article className="card">
            <h3>Portfolio unavailable</h3>
            <p>{state.errorMessage}</p>
          </article>
        ) : null}
        {state.status === "ready" && state.clients.length === 0 ? (
          <article className="card">
            <h3>No visible clients</h3>
            <p>No client grants are currently available for this session.</p>
          </article>
        ) : null}
        {state.status === "ready"
          ? state.clients.map((client) => (
              <article className="card" key={client.client_id}>
                <span className="eyebrow">Client</span>
                <h3>{client.name}</h3>
                <p className="muted-copy">Client ID: {client.client_id}</p>
                <p className="muted-copy">Fabric ID: {client.fabric_id}</p>
                <div className="card-stat-grid">
                  <div>
                    <strong>{client.open_alert_count}</strong>
                    <span>Open Alerts</span>
                  </div>
                  <div>
                    <strong>{client.critical_asset_count}</strong>
                    <span>Critical Assets</span>
                  </div>
                </div>
                <div className="card-actions">
                  <Link to={`/clients/${client.client_id}`}>Open Client</Link>
                  <Link to={`/clients/${client.client_id}/config`}>Config</Link>
                </div>
              </article>
            ))
          : null}
      </section>
    </>
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

import { useEffect, useReducer } from "react";
import { Link, useParams } from "react-router-dom";

import {
  ApiError,
  masOpsApiClient,
  type ActivityEventResponse,
  type AssetResponse,
  type ClientSummaryResponse,
  type IncidentResponse,
} from "../api/client";
import { AssetDetailCard, AssetListPanel } from "../assets";
import { type StreamFrame, useManagedStream } from "../streams";
import { ClientActivityPanel } from "./ClientActivityPanel";
import {
  clientDetailReducer,
  initialClientDetailState,
} from "./reducer";
import {
  parseActivityEventPayload,
  parseAssetPayload,
  parseClientSummaryPayload,
} from "./streamPayloads";

export function ClientPage() {
  const { clientId } = useParams();
  const [state, dispatch] = useReducer(
    clientDetailReducer,
    initialClientDetailState,
  );

  async function loadClientView(
    targetClientId: string,
    preferredAssetId: string | null,
  ) {
    try {
      const [client, incidents, assets, clientActivity] = await Promise.all([
        loadClientSummary(targetClientId),
        loadClientIncidents(targetClientId),
        loadClientAssets(targetClientId),
        loadClientActivity(targetClientId),
      ]);
      const selectedAssetId =
        preferredAssetId &&
        assets.some((asset) => asset.asset_id === preferredAssetId)
          ? preferredAssetId
          : assets[0]?.asset_id ?? null;
      const selectedAssetPanel =
        selectedAssetId === null
          ? { activity: [], asset: null }
          : await loadAssetPanel(selectedAssetId);
      dispatch({
        type: "loaded",
        assets,
        client,
        clientActivity,
        incidents,
        selectedAsset: selectedAssetPanel.asset,
        selectedAssetActivity: selectedAssetPanel.activity,
        selectedAssetId,
      });
    } catch (error) {
      console.error("Failed to load client detail", error);
      dispatch({ type: "failed", message: getClientErrorMessage(error) });
    }
  }

  useEffect(() => {
    if (!clientId) {
      dispatch({
        type: "failed",
        message: "No client id was provided in the route.",
      });
      return;
    }
    dispatch({ type: "loading" });
    void loadClientView(clientId, state.selectedAssetId);
  }, [clientId]);

  useManagedStream({
    enabled: clientId !== undefined,
    eventTypes: [
      "client.updated",
      "incident.updated",
      "asset.updated",
      "activity.appended",
    ],
    liveUrl: clientId ? masOpsApiClient.getClientStreamUrl(clientId) : "",
    replay(lastEventId) {
      if (!clientId) {
        return Promise.resolve(new Response(null, { status: 400 }));
      }
      return masOpsApiClient.fetchClientStream(clientId, {
        lastEventId,
        replayOnly: true,
      });
    },
    scopeKey: `client:${clientId ?? "unknown"}`,
    onEvent(frame) {
      void handleStreamEvent(frame);
    },
    onForbidden() {
      dispatch({
        type: "failed",
        message: "Client stream access was revoked during reconnect.",
      });
    },
  });

  async function handleStreamEvent(frame: StreamFrame) {
    if (!clientId) {
      return;
    }
    if (frame.event === "client.updated") {
      const client = parseClientSummaryPayload(frame.data.payload);
      if (client !== null) {
        dispatch({ type: "client_loaded", client });
        return;
      }
      await refreshClientSummary(clientId);
      return;
    }
    if (frame.event === "asset.updated") {
      const asset = parseAssetPayload(frame.data.payload);
      if (asset !== null) {
        dispatch({ type: "asset_replaced", asset });
        return;
      }
      await refreshAsset(frame.data.subject_id);
      return;
    }
    if (frame.event === "activity.appended") {
      const activity = parseActivityEventPayload(frame.data.payload);
      if (activity !== null) {
        dispatch({ type: "activity_appended", activity });
        return;
      }
      await refreshActivity(clientId);
      return;
    }
    if (frame.event === "incident.updated") {
      await refreshIncidents(clientId);
    }
  }

  async function handleAssetSelect(assetId: string) {
    dispatch({ type: "asset_selected", assetId });
    try {
      const panel = await loadAssetPanel(assetId);
      dispatch({ type: "asset_loaded", activity: panel.activity, asset: panel.asset });
    } catch (error) {
      console.error("Failed to load asset detail", error);
      dispatch({ type: "failed", message: getClientErrorMessage(error) });
    }
  }

  async function refreshActivity(targetClientId: string) {
    try {
      dispatch({
        type: "client_activity_loaded",
        activity: await loadClientActivity(targetClientId),
      });
      if (state.selectedAssetId !== null) {
        const panel = await loadAssetPanel(state.selectedAssetId);
        dispatch({ type: "asset_loaded", activity: panel.activity, asset: panel.asset });
      }
    } catch (error) {
      console.error("Failed to refresh client activity", error);
      dispatch({ type: "failed", message: getClientErrorMessage(error) });
    }
  }

  async function refreshAsset(assetId: string) {
    try {
      const asset = await loadAssetDetail(assetId);
      if (asset !== null) {
        dispatch({ type: "asset_replaced", asset });
      }
    } catch (error) {
      console.error("Failed to refresh asset detail", error);
      dispatch({ type: "failed", message: getClientErrorMessage(error) });
    }
  }

  async function refreshClientSummary(targetClientId: string) {
    try {
      dispatch({
        type: "client_loaded",
        client: await loadClientSummary(targetClientId),
      });
    } catch (error) {
      console.error("Failed to refresh client summary", error);
      dispatch({ type: "failed", message: getClientErrorMessage(error) });
    }
  }

  async function refreshIncidents(targetClientId: string) {
    try {
      dispatch({
        type: "incidents_loaded",
        incidents: await loadClientIncidents(targetClientId),
      });
    } catch (error) {
      console.error("Failed to refresh client incidents", error);
      dispatch({ type: "failed", message: getClientErrorMessage(error) });
    }
  }

  if (!clientId) {
    return renderClientError("No client id was provided in the route.");
  }
  if (state.status === "error") {
    return renderClientError(
      state.errorMessage ?? "The client view could not be loaded.",
    );
  }

  return (
    <>
      <section className="hero">
        <span className="eyebrow">Client</span>
        <h2>{state.client?.name ?? clientId}</h2>
        <p>
          Client summary, activity, asset inventory, and asset detail are backed by
          the Phase 2 visibility projections and client stream.
        </p>
        {state.status === "ready" && state.errorMessage ? (
          <p className="form-error">{state.errorMessage}</p>
        ) : null}
      </section>
      <section className="grid">
        <article className="card">
          <h3>Summary</h3>
          {state.status === "loading" ? <p>Loading client summary...</p> : null}
          {state.client ? (
            <>
              <p className="muted-copy">Client ID: {state.client.client_id}</p>
              <p className="muted-copy">Fabric ID: {state.client.fabric_id}</p>
              <p className="muted-copy">
                Alerts: {state.client.open_alert_count} | Critical assets:{" "}
                {state.client.critical_asset_count}
              </p>
            </>
          ) : null}
        </article>
        <article className="card">
          <h3>Config Console</h3>
          <p>
            Desired-state configuration is available for this client under the
            config route.
          </p>
          <Link to={`/clients/${clientId}/config`}>Open Config Console</Link>
        </article>
      </section>
      <section className="grid two-up">
        <article className="card">
          <h3>Incidents</h3>
          {state.status === "loading" ? <p>Loading incidents...</p> : null}
          {state.status === "ready" && state.incidents.length === 0 ? (
            <p className="muted-copy">No incidents are currently projected for this client.</p>
          ) : null}
          <div className="list">
            {state.incidents.map((incident) => (
              <Link
                className="list-item"
                key={incident.incident_id}
                to={`/incidents/${incident.incident_id}`}
              >
                <strong>{incident.summary}</strong>
                <span>
                  {incident.severity.toUpperCase()} | {incident.state}
                </span>
              </Link>
            ))}
          </div>
        </article>
        <article className="card">
          <h3>Assets</h3>
          {state.status === "loading" ? <p>Loading assets...</p> : null}
          <AssetListPanel
            assets={state.assets}
            onSelect={(assetId) => {
              void handleAssetSelect(assetId);
            }}
            selectedAssetId={state.selectedAssetId}
          />
        </article>
      </section>
      <section className="grid two-up">
        <ClientActivityPanel events={state.clientActivity} />
        <AssetDetailCard
          activity={state.selectedAssetActivity}
          asset={state.selectedAsset}
        />
      </section>
    </>
  );
}

async function loadAssetDetail(assetId: string): Promise<AssetResponse | null> {
  try {
    return await masOpsApiClient.getAsset(assetId);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      return null;
    }
    throw error;
  }
}

async function loadAssetActivity(
  assetId: string,
): Promise<Array<ActivityEventResponse>> {
  return masOpsApiClient.listAssetActivity(assetId);
}

async function loadAssetPanel(assetId: string): Promise<{
  activity: Array<ActivityEventResponse>;
  asset: AssetResponse | null;
}> {
  const [asset, activity] = await Promise.all([loadAssetDetail(assetId), loadAssetActivity(assetId)]);
  return { activity, asset };
}

async function loadClientActivity(
  clientId: string,
): Promise<Array<ActivityEventResponse>> {
  return masOpsApiClient.listClientActivity(clientId);
}

async function loadClientAssets(clientId: string): Promise<Array<AssetResponse>> {
  return masOpsApiClient.listClientAssets(clientId);
}

async function loadClientIncidents(
  clientId: string,
): Promise<Array<IncidentResponse>> {
  return masOpsApiClient.listClientIncidents(clientId);
}

async function loadClientSummary(
  clientId: string,
): Promise<ClientSummaryResponse> {
  return masOpsApiClient.getClient(clientId);
}

function renderClientError(message: string) {
  return (
    <section className="hero">
      <span className="eyebrow">Client</span>
      <h2>Client view unavailable</h2>
      <p>{message}</p>
    </section>
  );
}

function getClientErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 404) {
    return "The requested client or asset was not found.";
  }
  if (error instanceof ApiError && error.status === 403) {
    return "The current session is not authorized for this client.";
  }
  return "The ops API did not return client detail data.";
}

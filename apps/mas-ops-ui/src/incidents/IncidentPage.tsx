import { useEffect, useReducer } from "react";
import { useParams } from "react-router-dom";

import { ApiError, masOpsApiClient } from "../api/client";
import { IncidentChatPanel } from "../chat";
import { useManagedStream } from "../streams";
import {
  incidentPageReducer,
  initialIncidentPageState,
} from "./reducer";

export function IncidentPage() {
  const { incidentId = "" } = useParams();
  const [state, dispatch] = useReducer(
    incidentPageReducer,
    initialIncidentPageState,
  );

  async function loadIncidentView(targetIncidentId: string) {
    try {
      const [incident, activity] = await Promise.all([
        masOpsApiClient.getIncident(targetIncidentId),
        masOpsApiClient.listIncidentActivity(targetIncidentId),
      ]);
      dispatch({ type: "loaded", activity, incident });
    } catch (error) {
      console.error("Failed to load incident cockpit", error);
      dispatch({ type: "failed", message: getIncidentErrorMessage(error) });
    }
  }

  useEffect(() => {
    if (!incidentId) {
      dispatch({
        type: "failed",
        message: "No incident id was provided in the route.",
      });
      return;
    }
    dispatch({ type: "loading" });
    void loadIncidentView(incidentId);
  }, [incidentId]);

  useManagedStream({
    enabled: incidentId.length > 0,
    eventTypes: [
      "incident.updated",
      "activity.appended",
      "approval.requested",
      "approval.resolved",
    ],
    liveUrl:
      incidentId.length > 0 ? masOpsApiClient.getIncidentStreamUrl(incidentId) : "",
    replay(lastEventId) {
      if (!incidentId) {
        return Promise.resolve(new Response(null, { status: 400 }));
      }
      return masOpsApiClient.fetchIncidentStream(incidentId, {
        lastEventId,
        replayOnly: true,
      });
    },
    scopeKey: `incident:${incidentId || "unknown"}`,
    onEvent() {
      if (!incidentId) {
        return;
      }
      void loadIncidentView(incidentId);
    },
    onForbidden() {
      dispatch({
        type: "failed",
        message: "Incident stream access was revoked during reconnect.",
      });
    },
  });

  if (!incidentId) {
    return renderIncidentError("No incident id was provided in the route.");
  }
  if (state.status === "error") {
    return renderIncidentError(
      state.errorMessage ?? "The incident cockpit could not be loaded.",
    );
  }

  return (
    <>
      <section className="hero">
        <span className="eyebrow">Incident Cockpit</span>
        <h2>{state.incident?.summary ?? incidentId}</h2>
        <p>
          Incident detail, evidence, recommendations, and incident-scoped chat are
          loaded from the Phase 3 ops-plane API and incident stream.
        </p>
        {state.status === "ready" && state.errorMessage ? (
          <p className="form-error">{state.errorMessage}</p>
        ) : null}
      </section>
      <section className="grid">
        <article className="card">
          <h3>Summary</h3>
          {state.status === "loading" ? <p>Loading incident summary...</p> : null}
          {state.incident ? (
            <>
              <p className="muted-copy">Client ID: {state.incident.client_id}</p>
              <p className="muted-copy">Fabric ID: {state.incident.fabric_id}</p>
              <p className="muted-copy">
                Severity: {state.incident.severity} | State: {state.incident.state}
              </p>
              <p className="muted-copy">
                Assets: {state.incident.assets?.length ?? 0} | Evidence bundles:{" "}
                {state.incident.evidence_bundles?.length ?? 0}
              </p>
            </>
          ) : null}
        </article>
        <article className="card">
          <h3>Activity</h3>
          {state.status === "loading" ? <p>Loading activity timeline...</p> : null}
          {state.status === "ready" && state.activity.length === 0 ? (
            <p className="muted-copy">No timeline events are available for this incident.</p>
          ) : null}
          <div className="list">
            {state.activity.map((entry) => (
              <article className="list-item" key={entry.activity_id}>
                <strong>{entry.event_type}</strong>
                <span>{entry.occurred_at}</span>
              </article>
            ))}
          </div>
        </article>
      </section>
      <section className="grid two-up">
        <article className="card">
          <h3>Affected Assets</h3>
          {state.status === "loading" ? <p>Loading affected assets...</p> : null}
          {state.status === "ready" &&
          (state.incident?.assets?.length ?? 0) === 0 ? (
            <p className="muted-copy">No assets are currently linked to this incident.</p>
          ) : null}
          <div className="list">
            {state.incident?.assets?.map((asset) => (
              <article className="list-item" key={asset.asset_id}>
                <strong>{asset.hostname ?? asset.asset_id}</strong>
                <span>
                  {asset.vendor ?? "unknown"} | {asset.health_state}
                </span>
              </article>
            ))}
          </div>
        </article>
        <article className="card">
          <h3>Evidence</h3>
          {state.status === "loading" ? <p>Loading evidence bundles...</p> : null}
          {state.status === "ready" &&
          (state.incident?.evidence_bundles?.length ?? 0) === 0 ? (
            <p className="muted-copy">No structured evidence has been captured yet.</p>
          ) : null}
          <div className="list">
            {state.incident?.evidence_bundles?.map((bundle) => (
              <article className="list-item" key={bundle.evidence_bundle_id}>
                <strong>{bundle.summary}</strong>
                <span>{bundle.collected_at}</span>
              </article>
            ))}
          </div>
        </article>
      </section>
      <section className="grid">
        <article className="card">
          <h3>Recommended Actions</h3>
          {state.status === "ready" &&
          (state.incident?.recommended_actions?.length ?? 0) === 0 ? (
            <p className="muted-copy">No operator actions have been recommended yet.</p>
          ) : null}
          <div className="list">
            {state.incident?.recommended_actions?.map((action, index) => {
              const title =
                typeof action.title === "string"
                  ? action.title
                  : `Recommended action ${index + 1}`;
              const details =
                typeof action.details === "string" ? action.details : "";
              return (
                <article className="list-item" key={`${title}-${index}`}>
                  <strong>{title}</strong>
                  <span>{details}</span>
                </article>
              );
            })}
          </div>
        </article>
      </section>
      {state.incident ? (
        <IncidentChatPanel
          clientId={state.incident.client_id}
          fabricId={state.incident.fabric_id}
          incidentId={state.incident.incident_id}
        />
      ) : null}
    </>
  );
}

function renderIncidentError(message: string) {
  return (
    <section className="hero">
      <span className="eyebrow">Incident Cockpit</span>
      <h2>Incident unavailable</h2>
      <p>{message}</p>
    </section>
  );
}

function getIncidentErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 404) {
    return "The requested incident was not found.";
  }
  if (error instanceof ApiError && error.status === 403) {
    return "The current session is not authorized for this incident.";
  }
  return "The ops API did not return incident data.";
}

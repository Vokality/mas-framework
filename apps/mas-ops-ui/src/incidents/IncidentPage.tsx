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
          Incident detail, timeline, and incident-scoped chat are loaded from the
          Phase 1 ops-plane API and incident stream.
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

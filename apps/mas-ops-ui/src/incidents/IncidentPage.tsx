import { useEffect, useReducer } from "react";
import { useParams } from "react-router-dom";

import { ApiError, masOpsApiClient } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { IncidentChatPanel } from "../chat";
import { useManagedStream } from "../streams";
import {
  incidentPageReducer,
  initialIncidentPageState,
} from "./reducer";

export function IncidentPage() {
  const { incidentId = "" } = useParams();
  const auth = useAuth();
  const [state, dispatch] = useReducer(
    incidentPageReducer,
    initialIncidentPageState,
  );

  async function loadIncidentView(targetIncidentId: string) {
    try {
      const incident = await masOpsApiClient.getIncident(targetIncidentId);
      dispatch({ type: "loaded", incident });
    } catch (error) {
      console.error("Failed to load incident cockpit", error);
      dispatch({ type: "failed", message: getIncidentErrorMessage(error) });
    }
  }

  async function handleApprovalDecision(
    approvalId: string,
    decision: "approve" | "reject",
  ) {
    try {
      await masOpsApiClient.submitApprovalDecision(approvalId, { decision });
      await loadIncidentView(incidentId);
    } catch (error) {
      console.error("Failed to submit incident approval decision", error);
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
      "approval.executed",
      "approval.expired",
      "approval.cancelled",
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

  const hostAssets =
    state.incident?.assets?.filter(
      (asset) => asset.asset_kind === "linux_host" || asset.asset_kind === "windows_host",
    ) ?? [];
  const hostRemediationEvents = state.activity.filter((entry) =>
    entry.event_type.startsWith("remediation."),
  );

  return (
    <div className="page-stack">
      <section className="hero">
        <span className="eyebrow">Incident Cockpit</span>
        <h2>{state.incident?.summary ?? incidentId}</h2>
        <p className="hero-subtitle">
          Incident detail, evidence, approvals, recommendations, and incident-scoped
          chat are loaded from the ops-plane incident API and stream.
        </p>
        {state.status === "ready" && state.errorMessage ? (
          <p className="form-error">{state.errorMessage}</p>
        ) : null}
      </section>
      <section className="grid">
        <article className="card">
          <h3>Summary</h3>
          {state.status === "loading" ? (
            <p className="muted-copy">Loading incident summary...</p>
          ) : null}
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
          {state.status === "loading" ? (
            <p className="muted-copy">Loading activity timeline...</p>
          ) : null}
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
      {state.incident && (state.incident.approvals?.length ?? 0) > 0 ? (
        <section className="grid">
          <article className="card full-width-card">
            <h3>Approvals</h3>
            <div className="list">
              {state.incident.approvals?.map((approval) => (
                <article className="list-item" key={approval.approval_id}>
                  <strong>
                    {approval.title} | {approval.state}
                  </strong>
                  <span>{approval.risk_summary}</span>
                  {approval.state === "pending" && auth.session?.role !== "viewer" ? (
                    <div className="card-actions">
                      <button
                        onClick={() => {
                          void handleApprovalDecision(approval.approval_id, "approve");
                        }}
                      >
                        Approve
                      </button>
                      <button
                        className="secondary-button"
                        onClick={() => {
                          void handleApprovalDecision(approval.approval_id, "reject");
                        }}
                      >
                        Reject
                      </button>
                    </div>
                  ) : null}
                </article>
              ))}
            </div>
          </article>
        </section>
      ) : null}
      <section className="grid two-up">
        <article className="card">
          <h3>Affected Assets</h3>
          {state.status === "loading" ? (
            <p className="muted-copy">Loading affected assets...</p>
          ) : null}
          {state.status === "ready" &&
          (state.incident?.assets?.length ?? 0) === 0 ? (
            <p className="muted-copy">No assets are currently linked to this incident.</p>
          ) : null}
          <div className="list">
            {state.incident?.assets?.map((asset) => (
              <article className="list-item" key={asset.asset_id}>
                <strong>{asset.hostname ?? asset.asset_id}</strong>
                <span>
                  {asset.vendor ?? "unknown"} | {asset.asset_kind} | {asset.health_state}
                </span>
              </article>
            ))}
          </div>
        </article>
        <article className="card">
          <h3>Evidence</h3>
          {state.status === "loading" ? (
            <p className="muted-copy">Loading evidence bundles...</p>
          ) : null}
          {state.status === "ready" &&
          (state.incident?.evidence_bundles?.length ?? 0) === 0 ? (
            <p className="muted-copy">No structured evidence has been captured yet.</p>
          ) : null}
          <div className="list">
            {state.incident?.evidence_bundles?.map((bundle) => (
              <article className="list-item" key={bundle.evidence_bundle_id}>
                <strong>{bundle.summary}</strong>
                <span>{bundle.collected_at}</span>
                {renderHostEvidence(bundle.items)}
              </article>
            ))}
          </div>
        </article>
      </section>
      {hostAssets.length > 0 ? (
        <section className="grid">
          <article className="card full-width-card">
            <h3>Host Remediation Timeline</h3>
            {hostRemediationEvents.length === 0 ? (
              <p className="muted-copy">No host remediation actions have been recorded yet.</p>
            ) : (
              <div className="list">
                {hostRemediationEvents.map((event) => (
                  <article className="list-item" key={`host-remediation-${event.activity_id}`}>
                    <strong>{event.event_type}</strong>
                    <span>{describeHostRemediationEvent(event.payload)}</span>
                  </article>
                ))}
              </div>
            )}
          </article>
        </section>
      ) : null}
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
    </div>
  );
}

function renderIncidentError(message: string) {
  return (
    <section className="hero">
      <span className="eyebrow">Incident Cockpit</span>
      <h2>Incident unavailable</h2>
      <p className="hero-subtitle">{message}</p>
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

function renderHostEvidence(items: Array<Record<string, unknown>>) {
  const hostServices = items.find((item) => item.kind === "host_services");
  if (!hostServices || !Array.isArray(hostServices.services)) {
    return null;
  }
  const services = hostServices.services
    .map((service) => {
      if (
        typeof service !== "object" ||
        service === null ||
        typeof service.service_name !== "string" ||
        typeof service.service_state !== "string"
      ) {
        return null;
      }
      return `${service.service_name}: ${service.service_state}`;
    })
    .filter((service): service is string => service !== null);
  if (services.length === 0) {
    return null;
  }
  return <span>{services.join(" | ")}</span>;
}

function describeHostRemediationEvent(payload: Record<string, unknown>): string {
  const serviceName =
    typeof payload.service_name === "string"
      ? payload.service_name
      : typeof (payload.post_state as { service_name?: unknown } | undefined)?.service_name ===
          "string"
        ? ((payload.post_state as { service_name: string }).service_name)
        : "service";
  const serviceState =
    typeof (payload.post_state as { service_state?: unknown } | undefined)?.service_state ===
    "string"
      ? ((payload.post_state as { service_state: string }).service_state)
      : null;
  if (serviceState) {
    return `${serviceName} -> ${serviceState}`;
  }
  return serviceName;
}

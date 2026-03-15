import { useEffect, useReducer } from "react";
import { useParams } from "react-router-dom";

import {
  ApiError,
  masOpsApiClient,
  type ConfigDesiredStateInput,
} from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { useManagedStream } from "../streams";
import {
  configPageReducer,
  initialConfigPageState,
} from "./reducer";

export function ConfigPage() {
  const { clientId } = useParams();
  const auth = useAuth();
  const [state, dispatch] = useReducer(
    configPageReducer,
    initialConfigPageState,
  );

  async function loadDesiredState(targetClientId: string) {
    try {
      const desiredState = await masOpsApiClient.getDesiredState(targetClientId);
      dispatch({ type: "desired_state_loaded", desiredState });
    } catch (error) {
      console.error("Failed to load desired-state configuration", error);
      dispatch({ type: "failed", message: getConfigErrorMessage(error) });
    }
  }

  async function loadRunHistory(targetClientId: string) {
    try {
      const runHistory = await masOpsApiClient.listConfigRuns(targetClientId);
      dispatch({ type: "run_history_loaded", runHistory });
    } catch (error) {
      console.error("Failed to load config run history", error);
      dispatch({ type: "failed", message: getConfigErrorMessage(error) });
    }
  }

  async function loadConfigConsole(targetClientId: string) {
    try {
      const [desiredState, runHistory] = await Promise.all([
        masOpsApiClient.getDesiredState(targetClientId),
        masOpsApiClient.listConfigRuns(targetClientId),
      ]);
      dispatch({ type: "loaded", desiredState, runHistory });
    } catch (error) {
      console.error("Failed to load desired-state configuration", error);
      dispatch({ type: "failed", message: getConfigErrorMessage(error) });
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
    void loadConfigConsole(clientId);
  }, [clientId]);

  useManagedStream({
    enabled: clientId !== undefined && auth.canAccessClient(clientId),
    eventTypes: ["client.updated"],
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
    scopeKey: `config:${clientId ?? "unknown"}`,
    onEvent(frame) {
      if (!clientId) {
        return;
      }
      if (frame.data.subject_type === "config_desired_state") {
        void loadDesiredState(clientId);
        return;
      }
      if (
        frame.data.subject_type === "config_validation_run" ||
        frame.data.subject_type === "config_apply_run" ||
        frame.data.subject_type === "config_apply_step"
      ) {
        void loadRunHistory(clientId);
      }
    },
    onForbidden() {
      dispatch({
        type: "failed",
        message: "Config stream access was revoked during reconnect.",
      });
    },
  });

  async function handleSave() {
    if (!clientId) {
      return;
    }
    dispatch({ type: "save_started" });
    try {
      const payload = JSON.parse(state.editorValue) as ConfigDesiredStateInput;
      const desiredState = await masOpsApiClient.replaceDesiredState(clientId, payload);
      dispatch({ type: "desired_state_loaded", desiredState });
    } catch (error) {
      console.error("Failed to save desired-state configuration", error);
      dispatch({
        type: "failed",
        message:
          error instanceof SyntaxError
            ? "The desired-state document is not valid JSON."
            : getConfigErrorMessage(error),
      });
    } finally {
      dispatch({ type: "save_finished" });
    }
  }

  async function handleValidate() {
    if (!clientId) {
      return;
    }
    try {
      await masOpsApiClient.validateDesiredState(clientId);
      await loadRunHistory(clientId);
    } catch (error) {
      console.error("Failed to start validation run", error);
      dispatch({ type: "failed", message: getConfigErrorMessage(error) });
    }
  }

  async function handleApply() {
    if (!clientId) {
      return;
    }
    try {
      await masOpsApiClient.applyDesiredState(clientId);
      await loadRunHistory(clientId);
    } catch (error) {
      console.error("Failed to start apply run", error);
      dispatch({ type: "failed", message: getConfigErrorMessage(error) });
    }
  }

  async function handleCancelApplyRun(configApplyRunId: string) {
    if (!clientId) {
      return;
    }
    try {
      await masOpsApiClient.cancelConfigApplyRun(clientId, configApplyRunId, {
        reason: "Cancelled from config console",
      });
      await loadRunHistory(clientId);
    } catch (error) {
      console.error("Failed to cancel config apply run", error);
      dispatch({ type: "failed", message: getConfigErrorMessage(error) });
    }
  }

  if (!clientId) {
    return renderConfigError("No client id was provided in the route.");
  }
  if (!auth.canAccessClient(clientId)) {
    return renderConfigError("The current session is not authorized for this client.");
  }
  if (state.status === "error") {
    return renderConfigError(
      state.errorMessage ?? "The desired-state console could not be loaded.",
    );
  }

  const isAdmin = auth.session?.role === "admin";
  const latestValidation = state.runHistory?.validation_runs?.[0] ?? null;
  const latestApply = state.runHistory?.apply_runs?.[0] ?? null;

  return (
    <>
      <section className="hero">
        <span className="eyebrow">Desired State</span>
        <h2>Config console for {clientId}</h2>
        <p>
          Desired-state persistence, validation, apply history, and approval-gated
          execution are backed by the phase-4 config APIs and client stream.
        </p>
      </section>
      <section className="grid">
        <article className="card">
          <h3>Current State</h3>
          {state.status === "loading" ? <p>Loading desired-state document...</p> : null}
          {state.desiredState ? (
            <p className="muted-copy">
              Version {state.desiredState.desired_state_version} | Fabric{" "}
              {state.desiredState.fabric_id}
            </p>
          ) : null}
          {state.errorMessage ? <p className="form-error">{state.errorMessage}</p> : null}
        </article>
        <article className="card">
          <h3>Runs</h3>
          <p className="muted-copy">
            Validation: {latestValidation?.status ?? "not started"}
            <br />
            Apply: {latestApply?.status ?? "not started"}
          </p>
          {latestApply?.approval_id ? (
            <p className="muted-copy">Approval: {latestApply.approval_id}</p>
          ) : null}
          {isAdmin ? (
            <div className="card-actions">
              <button
                onClick={() => {
                  void handleValidate();
                }}
              >
                Validate
              </button>
              <button
                className="secondary-button"
                onClick={() => {
                  void handleApply();
                }}
              >
                Apply
              </button>
              {latestApply &&
              (latestApply.status === "pending" ||
                latestApply.status === "validating") ? (
                <button
                  className="secondary-button"
                  onClick={() => {
                    void handleCancelApplyRun(latestApply.config_apply_run_id);
                  }}
                >
                  Cancel Pending Apply
                </button>
              ) : null}
            </div>
          ) : (
            <p className="muted-copy">Only admins can validate or apply configuration.</p>
          )}
        </article>
      </section>
      <section className="grid">
        <article className="card full-width-card">
          <h3>Desired-State Document</h3>
          <textarea
            className="editor"
            disabled={!isAdmin || state.saveStatus === "saving"}
            onChange={(event) => {
              dispatch({ type: "editor_changed", value: event.target.value });
            }}
            value={state.editorValue}
          />
          {isAdmin ? (
            <div className="card-actions">
              <button
                disabled={state.saveStatus === "saving"}
                onClick={() => {
                  void handleSave();
                }}
              >
                {state.saveStatus === "saving" ? "Saving..." : "Save Desired State"}
              </button>
            </div>
          ) : null}
        </article>
      </section>
      <section className="grid">
        <article className="card full-width-card">
          <h3>Run History</h3>
          {state.runHistory &&
          (state.runHistory.validation_runs?.length ?? 0) === 0 &&
          (state.runHistory.apply_runs?.length ?? 0) === 0 ? (
            <p className="muted-copy">No validation or apply runs have been recorded yet.</p>
          ) : null}
          <div className="list">
            {state.runHistory?.apply_runs?.map((run) => (
              <article className="list-item" key={run.config_apply_run_id}>
                <strong>
                  Apply v{run.desired_state_version} | {run.status}
                </strong>
                <span>{run.requested_at}</span>
                {(run.steps?.length ?? 0) > 0 ? (
                  <span>
                    Steps: {run.steps?.map((step) => step.step_name).join(", ")}
                  </span>
                ) : null}
                {isAdmin &&
                (run.status === "pending" || run.status === "validating") ? (
                  <span className="card-actions">
                    <button
                      className="secondary-button"
                      onClick={() => {
                        void handleCancelApplyRun(run.config_apply_run_id);
                      }}
                    >
                      Cancel Run
                    </button>
                  </span>
                ) : null}
              </article>
            ))}
            {state.runHistory?.validation_runs?.map((run) => (
              <article className="list-item" key={run.config_apply_run_id}>
                <strong>
                  Validation v{run.desired_state_version} | {run.status}
                </strong>
                <span>{run.validated_at}</span>
              </article>
            ))}
          </div>
        </article>
      </section>
    </>
  );
}

function renderConfigError(message: string) {
  return (
    <section className="hero">
      <span className="eyebrow">Desired State</span>
      <h2>Config console unavailable</h2>
      <p>{message}</p>
    </section>
  );
}

function getConfigErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 403) {
    return "The current session cannot access this desired-state configuration.";
  }
  if (error instanceof ApiError && error.status === 404) {
    return "No desired-state document exists for this client.";
  }
  if (error instanceof ApiError && error.status === 409) {
    return "The desired-state version is stale and must be refreshed.";
  }
  return "The ops API did not return desired-state data.";
}

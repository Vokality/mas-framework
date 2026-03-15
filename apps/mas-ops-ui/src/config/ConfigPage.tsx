import { useEffect, useReducer } from "react";
import { useParams } from "react-router-dom";

import {
  ApiError,
  masOpsApiClient,
  type ConfigApplyResult,
  type ConfigDesiredStateInput,
  type ConfigValidationResult,
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
      dispatch({ type: "loaded", desiredState });
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
    void loadDesiredState(clientId);
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
      if (frame.data.subject_type === "config_validation_run") {
        dispatch({
          type: "validation_loaded",
          result: {
            client_id: clientId,
            config_apply_run_id: String(
              frame.data.payload.config_apply_run_id ?? frame.data.subject_id,
            ),
            desired_state_version: Number(
              frame.data.payload.desired_state_version ?? 0,
            ),
            status: String(frame.data.payload.status ?? "unknown"),
            validated_at: frame.data.occurred_at,
            errors: [],
            warnings: [],
          },
        });
        return;
      }
      if (frame.data.subject_type === "config_apply_run") {
        dispatch({
          type: "apply_loaded",
          result: {
            client_id: clientId,
            completed_at: null,
            config_apply_run_id: String(
              frame.data.payload.config_apply_run_id ?? frame.data.subject_id,
            ),
            desired_state_version: Number(
              frame.data.payload.desired_state_version ?? 0,
            ),
            error_summary: null,
            started_at: frame.data.occurred_at,
            status:
              String(frame.data.payload.status ?? "pending") as ConfigApplyResult["status"],
          },
        });
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
      dispatch({ type: "loaded", desiredState });
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
      const result = await masOpsApiClient.validateDesiredState(clientId);
      dispatch({ type: "validation_loaded", result });
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
      const result = await masOpsApiClient.applyDesiredState(clientId);
      dispatch({ type: "apply_loaded", result });
    } catch (error) {
      console.error("Failed to start apply run", error);
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

  return (
    <>
      <section className="hero">
        <span className="eyebrow">Desired State</span>
        <h2>Config console for {clientId}</h2>
        <p>
          Desired-state persistence, validation, and apply runs are backed by the
          Phase 1 config APIs and client stream.
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
            Validation: {state.validationResult?.status ?? "not started"}
            <br />
            Apply: {state.applyResult?.status ?? "not started"}
          </p>
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

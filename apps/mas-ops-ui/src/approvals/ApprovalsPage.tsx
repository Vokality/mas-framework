import { useEffect, useReducer } from "react";

import {
  ApiError,
  masOpsApiClient,
} from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import {
  approvalsReducer,
  initialApprovalsState,
} from "./reducer";

export function ApprovalsPage() {
  const auth = useAuth();
  const [state, dispatch] = useReducer(
    approvalsReducer,
    initialApprovalsState,
  );

  async function loadApprovals() {
    try {
      const approvals = await masOpsApiClient.listApprovals();
      dispatch({ type: "loaded", approvals });
    } catch (error) {
      console.error("Failed to load approvals inbox", error);
      dispatch({ type: "failed", message: getApprovalsErrorMessage(error) });
    }
  }

  useEffect(() => {
    dispatch({ type: "loading" });
    void loadApprovals();
  }, []);

  async function handleDecision(
    approvalId: string,
    decision: "approve" | "reject",
  ) {
    try {
      const approval = await masOpsApiClient.submitApprovalDecision(approvalId, {
        decision,
      });
      dispatch({ type: "approval_updated", approval });
    } catch (error) {
      console.error("Failed to submit approval decision", error);
      dispatch({ type: "failed", message: getApprovalsErrorMessage(error) });
    }
  }

  return (
    <div className="page-stack">
      <section className="hero">
        <span className="eyebrow">Approvals</span>
        <h2>Approval inbox</h2>
        <p className="hero-subtitle">
          Approval requests are listed with full phase-4 lifecycle state, and
          operators or admins can resolve visible items.
        </p>
        {state.status === "ready" && state.errorMessage ? (
          <p className="form-error">{state.errorMessage}</p>
        ) : null}
      </section>
      <section className="grid">
        {state.status === "loading" ? (
          <article className="card">
            <h3>Loading approvals</h3>
            <p className="muted-copy">
              Fetching approval requests visible to the current session.
            </p>
          </article>
        ) : null}
        {state.status === "error" ? (
          <article className="card">
            <h3>Approval inbox unavailable</h3>
            <p className="muted-copy">{state.errorMessage}</p>
          </article>
        ) : null}
        {state.status === "ready" && state.approvals.length === 0 ? (
          <article className="card">
            <h3>No approvals waiting</h3>
            <p className="muted-copy">
              No approval requests are currently visible to this session.
            </p>
          </article>
        ) : null}
        {state.status === "ready"
          ? state.approvals.map((approval) => (
              <article className="card" key={approval.approval_id}>
                <span className="eyebrow">{approval.state}</span>
                <h3>{approval.title}</h3>
                <p className="muted-copy">{approval.risk_summary}</p>
                <p className="muted-copy">
                  Client: {approval.client_id}
                  <br />
                  Action: {approval.action_kind}
                </p>
                {auth.session?.role !== "viewer" && approval.state === "pending" ? (
                  <div className="card-actions">
                    <button
                      onClick={() => {
                        void handleDecision(approval.approval_id, "approve");
                      }}
                    >
                      Approve
                    </button>
                    <button
                      className="secondary-button"
                      onClick={() => {
                        void handleDecision(approval.approval_id, "reject");
                      }}
                    >
                      Reject
                    </button>
                  </div>
                ) : null}
              </article>
            ))
          : null}
      </section>
    </div>
  );
}

function getApprovalsErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 403) {
    return "The current session cannot view or change these approvals.";
  }
  return "The ops API did not return approval data.";
}

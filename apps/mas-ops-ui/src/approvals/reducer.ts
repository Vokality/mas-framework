import type { ApprovalResponse } from "../api/client";

export type ApprovalsState = {
  approvals: Array<ApprovalResponse>;
  errorMessage: string | null;
  status: "loading" | "ready" | "error";
};

export type ApprovalsAction =
  | { type: "loading" }
  | { type: "loaded"; approvals: Array<ApprovalResponse> }
  | { type: "approval_updated"; approval: ApprovalResponse }
  | { type: "failed"; message: string };

export const initialApprovalsState: ApprovalsState = {
  approvals: [],
  errorMessage: null,
  status: "loading",
};

export function approvalsReducer(
  state: ApprovalsState,
  action: ApprovalsAction,
): ApprovalsState {
  switch (action.type) {
    case "loading":
      return { ...state, errorMessage: null, status: "loading" };
    case "loaded":
      return { approvals: action.approvals, errorMessage: null, status: "ready" };
    case "approval_updated":
      return {
        ...state,
        approvals: state.approvals.map((approval) =>
          approval.approval_id === action.approval.approval_id
            ? action.approval
            : approval,
        ),
      };
    case "failed":
      return {
        ...state,
        errorMessage: action.message,
        status: state.approvals.length > 0 ? "ready" : "error",
      };
  }
}

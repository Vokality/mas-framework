import type { ActivityEventResponse, IncidentDetailResponse } from "../api/client";

export type IncidentPageState = {
  activity: Array<ActivityEventResponse>;
  errorMessage: string | null;
  incident: IncidentDetailResponse | null;
  status: "loading" | "ready" | "error";
};

export type IncidentPageAction =
  | { type: "loaded"; incident: IncidentDetailResponse }
  | { type: "loading" }
  | { type: "failed"; message: string };

export const initialIncidentPageState: IncidentPageState = {
  activity: [],
  errorMessage: null,
  incident: null,
  status: "loading",
};

export function incidentPageReducer(
  state: IncidentPageState,
  action: IncidentPageAction,
): IncidentPageState {
  switch (action.type) {
    case "loaded":
      return {
        activity: action.incident.activity ?? [],
        errorMessage: null,
        incident: action.incident,
        status: "ready",
      };
    case "loading":
      return { ...state, errorMessage: null, status: "loading" };
    case "failed":
      return {
        ...state,
        errorMessage: action.message,
        status:
          state.incident !== null || state.activity.length > 0 ? "ready" : "error",
      };
  }
}

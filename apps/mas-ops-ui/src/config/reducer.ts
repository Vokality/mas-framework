import type {
  ConfigDesiredStateOutput,
  ConfigRunHistoryResponse,
} from "../api/client";

export type ConfigPageState = {
  desiredState: ConfigDesiredStateOutput | null;
  editorValue: string;
  errorMessage: string | null;
  runHistory: ConfigRunHistoryResponse | null;
  saveStatus: "idle" | "saving";
  status: "loading" | "ready" | "error";
};

export type ConfigPageAction =
  | { type: "loading" }
  | {
      type: "loaded";
      desiredState: ConfigDesiredStateOutput;
      runHistory: ConfigRunHistoryResponse;
    }
  | { type: "desired_state_loaded"; desiredState: ConfigDesiredStateOutput }
  | { type: "run_history_loaded"; runHistory: ConfigRunHistoryResponse }
  | { type: "editor_changed"; value: string }
  | { type: "save_started" }
  | { type: "save_finished" }
  | { type: "failed"; message: string };

export const initialConfigPageState: ConfigPageState = {
  desiredState: null,
  editorValue: "",
  errorMessage: null,
  runHistory: null,
  saveStatus: "idle",
  status: "loading",
};

export function configPageReducer(
  state: ConfigPageState,
  action: ConfigPageAction,
): ConfigPageState {
  switch (action.type) {
    case "loading":
      return { ...state, errorMessage: null, status: "loading" };
    case "loaded":
      return {
        ...state,
        desiredState: action.desiredState,
        editorValue: JSON.stringify(action.desiredState, null, 2),
        errorMessage: null,
        runHistory: action.runHistory,
        status: "ready",
      };
    case "desired_state_loaded":
      return {
        ...state,
        desiredState: action.desiredState,
        editorValue: JSON.stringify(action.desiredState, null, 2),
        errorMessage: null,
        status: "ready",
      };
    case "run_history_loaded":
      return { ...state, errorMessage: null, runHistory: action.runHistory, status: "ready" };
    case "editor_changed":
      return { ...state, editorValue: action.value };
    case "save_started":
      return { ...state, errorMessage: null, saveStatus: "saving" };
    case "save_finished":
      return { ...state, saveStatus: "idle" };
    case "failed":
      return {
        ...state,
        errorMessage: action.message,
        status: state.desiredState !== null ? "ready" : "error",
      };
  }
}

import type {
  ConfigApplyResult,
  ConfigDesiredStateOutput,
  ConfigValidationResult,
} from "../api/client";

export type ConfigPageState = {
  applyResult: ConfigApplyResult | null;
  desiredState: ConfigDesiredStateOutput | null;
  editorValue: string;
  errorMessage: string | null;
  saveStatus: "idle" | "saving";
  status: "loading" | "ready" | "error";
  validationResult: ConfigValidationResult | null;
};

export type ConfigPageAction =
  | { type: "loading" }
  | { type: "loaded"; desiredState: ConfigDesiredStateOutput }
  | { type: "editor_changed"; value: string }
  | { type: "save_started" }
  | { type: "save_finished" }
  | { type: "validation_loaded"; result: ConfigValidationResult }
  | { type: "apply_loaded"; result: ConfigApplyResult }
  | { type: "failed"; message: string };

export const initialConfigPageState: ConfigPageState = {
  applyResult: null,
  desiredState: null,
  editorValue: "",
  errorMessage: null,
  saveStatus: "idle",
  status: "loading",
  validationResult: null,
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
        status: "ready",
      };
    case "editor_changed":
      return { ...state, editorValue: action.value };
    case "save_started":
      return { ...state, errorMessage: null, saveStatus: "saving" };
    case "save_finished":
      return { ...state, saveStatus: "idle" };
    case "validation_loaded":
      return { ...state, validationResult: action.result };
    case "apply_loaded":
      return { ...state, applyResult: action.result };
    case "failed":
      return {
        ...state,
        errorMessage: action.message,
        status: state.desiredState !== null ? "ready" : "error",
      };
  }
}

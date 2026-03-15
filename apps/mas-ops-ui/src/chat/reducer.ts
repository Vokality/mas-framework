import type { ChatSessionResponse } from "../api/client";

export type ChatPanelState = {
  errorMessage: string | null;
  session: ChatSessionResponse | null;
  status: "loading" | "idle" | "creating" | "sending" | "error";
};

export type ChatPanelAction =
  | { type: "loading" }
  | { type: "idle" }
  | { type: "creating" }
  | { type: "sending" }
  | { type: "session_loaded"; session: ChatSessionResponse }
  | { type: "failed"; message: string };

export const initialChatPanelState: ChatPanelState = {
  errorMessage: null,
  session: null,
  status: "loading",
};

export function chatPanelReducer(
  state: ChatPanelState,
  action: ChatPanelAction,
): ChatPanelState {
  switch (action.type) {
    case "loading":
      return { ...state, status: "loading", errorMessage: null };
    case "idle":
      return { ...state, status: "idle" };
    case "creating":
      return { ...state, status: "creating", errorMessage: null };
    case "sending":
      return { ...state, status: "sending", errorMessage: null };
    case "session_loaded":
      return {
        errorMessage: null,
        session: action.session,
        status: "idle",
      };
    case "failed":
      return { ...state, status: "error", errorMessage: action.message };
  }
}

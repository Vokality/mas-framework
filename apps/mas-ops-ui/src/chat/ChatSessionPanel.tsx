import { useEffect, useReducer, useState, type FormEvent } from "react";

import {
  ApiError,
  masOpsApiClient,
  type ChatScope,
  type ChatSessionCreateRequest,
} from "../api/client";
import { useManagedStream } from "../streams";
import {
  chatPanelReducer,
  initialChatPanelState,
} from "./reducer";
import {
  clearChatSessionId,
  readChatSessionId,
  writeChatSessionId,
} from "./storage";

type ChatSessionPanelProps = {
  createPayload: ChatSessionCreateRequest;
  description: string;
  emptyStateMessage: string;
  placeholder: string;
  storageKey: string;
  title: string;
};

export function ChatSessionPanel({
  createPayload,
  description,
  emptyStateMessage,
  placeholder,
  storageKey,
  title,
}: ChatSessionPanelProps) {
  const [state, dispatch] = useReducer(chatPanelReducer, initialChatPanelState);
  const [message, setMessage] = useState("");

  async function loadSession(chatSessionId: string) {
    const session = await masOpsApiClient.getChatSession(chatSessionId);
    writeChatSessionId(storageKey, session.chat_session_id);
    dispatch({ type: "session_loaded", session });
    return session;
  }

  useEffect(() => {
    let active = true;
    const storedSessionId = readChatSessionId(storageKey);

    if (storedSessionId === null) {
      dispatch({ type: "idle" });
      return;
    }

    dispatch({ type: "loading" });

    async function restoreStoredSession(chatSessionId: string) {
      try {
        const session = await masOpsApiClient.getChatSession(chatSessionId);
        if (!active) {
          return;
        }
        dispatch({ type: "session_loaded", session });
      } catch (error) {
        if (!active) {
          return;
        }
        if (
          error instanceof ApiError &&
          (error.status === 403 || error.status === 404)
        ) {
          clearChatSessionId(storageKey);
          dispatch({ type: "idle" });
          return;
        }
        console.error("Failed to restore stored chat session", error);
        dispatch({ type: "failed", message: getChatErrorMessage(error) });
      }
    }

    void restoreStoredSession(storedSessionId);
    return () => {
      active = false;
    };
  }, [storageKey]);

  async function ensureSession() {
    if (state.session !== null) {
      return state.session;
    }
    dispatch({ type: "creating" });
    const createdSession = await masOpsApiClient.createChatSession(createPayload);
    writeChatSessionId(storageKey, createdSession.chat_session_id);
    dispatch({ type: "session_loaded", session: createdSession });
    return createdSession;
  }

  useManagedStream({
    enabled: state.session !== null,
    eventTypes: [
      "chat.delta",
      "chat.completed",
      "approval.requested",
      "approval.resolved",
      "approval.executed",
      "approval.expired",
      "approval.cancelled",
    ],
    liveUrl:
      state.session === null
        ? ""
        : masOpsApiClient.getChatStreamUrl(state.session.chat_session_id),
    replay(lastEventId) {
      if (state.session === null) {
        return Promise.resolve(new Response(null, { status: 400 }));
      }
      return masOpsApiClient.fetchChatStream(state.session.chat_session_id, {
        lastEventId,
        replayOnly: true,
      });
    },
    scopeKey:
      state.session === null
        ? `chat:${storageKey}`
        : `chat:${state.session.chat_session_id}`,
    onEvent(frame) {
      if (state.session === null) {
        return;
      }
      void loadSession(state.session.chat_session_id);
    },
    onForbidden() {
      clearChatSessionId(storageKey);
      dispatch({ type: "failed", message: "Chat access was revoked." });
    },
    onError(messageText) {
      dispatch({ type: "failed", message: messageText });
    },
  });

  async function handleRefresh() {
    try {
      if (state.session !== null) {
        await loadSession(state.session.chat_session_id);
      } else {
        await ensureSession();
      }
    } catch (error) {
      console.error("Failed to refresh chat session", error);
      dispatch({ type: "failed", message: getChatErrorMessage(error) });
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    dispatch({ type: "sending" });

    try {
      const session = await ensureSession();
      await masOpsApiClient.appendChatMessage(session.chat_session_id, { message });
      await loadSession(session.chat_session_id);
      setMessage("");
    } catch (error) {
      console.error("Failed to append chat message", error);
      dispatch({ type: "failed", message: getChatErrorMessage(error) });
    }
  }

  return (
    <section className="grid">
      <article className="card full-width-card chat-session-panel">
        <span className="eyebrow">{formatScopeLabel(createPayload.scope)}</span>
        <h3>{title}</h3>
        <p className="muted-copy">{description}</p>
        <p className="muted-copy">
          {state.session
            ? `Session ${state.session.chat_session_id}`
            : emptyStateMessage}
        </p>
        {state.errorMessage ? <p className="form-error">{state.errorMessage}</p> : null}
        <div className="card-actions">
          <button
            disabled={state.status === "creating" || state.status === "sending"}
            onClick={() => {
              void handleRefresh();
            }}
            type="button"
          >
            {state.session ? "Refresh Session" : "Create Session"}
          </button>
        </div>
        <div className="chat-session-messages list">
          {state.session && state.session.messages.length > 0 ? (
            state.session.messages.map((item) => (
              <article className="list-item chat-message" key={item.message_id}>
                <span className="chat-message-role">{item.role}</span>
                <span>{item.content}</span>
              </article>
            ))
          ) : (
            <p className="muted-copy">Messages will appear here after a session is created.</p>
          )}
        </div>
        <form className="chat-form" onSubmit={handleSubmit}>
          <textarea
            className="editor"
            onChange={(event) => {
              setMessage(event.target.value);
            }}
            placeholder={placeholder}
            value={message}
          />
          <div className="card-actions">
            <button
              disabled={state.status === "creating" || state.status === "sending" || message.trim().length === 0}
              type="submit"
            >
              {state.status === "sending" ? "Sending..." : "Send Operator Message"}
            </button>
          </div>
        </form>
      </article>
    </section>
  );
}

function formatScopeLabel(scope: ChatScope): string {
  return scope === "incident" ? "Incident Chat" : "Global Chat";
}

function getChatErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 403) {
    return "The current session cannot access this chat session.";
  }
  if (error instanceof ApiError && error.status === 404) {
    return "The stored chat session no longer exists.";
  }
  return "The ops API did not complete the chat request.";
}

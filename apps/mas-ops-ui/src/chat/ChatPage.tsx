import { useAuth } from "../auth/AuthProvider";
import { ChatSessionPanel } from "./ChatSessionPanel";

export function ChatPage() {
  const auth = useAuth();

  return (
    <>
      <section className="hero">
        <span className="eyebrow">Global Chat</span>
        <h2>Portfolio assistant shell</h2>
        <p>
          Global chat sessions are persisted through the Phase 1 chat API.
          Assistant-side reasoning remains deferred to Phase 3.
        </p>
      </section>
      <ChatSessionPanel
        createPayload={{ scope: "global" }}
        description="This shell is backed by persisted chat sessions and messages."
        emptyStateMessage="No global chat session has been created yet."
        placeholder="Describe what the operator needs to investigate next."
        storageKey={`global:${auth.session?.user_id ?? "anonymous"}`}
        title="Global Chat Session"
      />
    </>
  );
}

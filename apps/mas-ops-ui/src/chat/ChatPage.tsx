import { useAuth } from "../auth/AuthProvider";
import { ChatSessionPanel } from "./ChatSessionPanel";

export function ChatPage() {
  const auth = useAuth();

  return (
    <>
      <section className="hero">
        <span className="eyebrow">Global Chat</span>
        <h2>Portfolio assistant</h2>
        <p>
          Global chat sessions are persisted through the ops-plane chat API and
          answered from authorized Postgres-backed portfolio read models.
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

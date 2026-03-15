import { useAuth } from "../auth/AuthProvider";
import { ChatSessionPanel } from "./ChatSessionPanel";

export function ChatPage() {
  const auth = useAuth();

  return (
    <>
      <section className="hero">
        <span className="eyebrow">Portfolio Chat</span>
        <h2>Read-only portfolio summary</h2>
        <p>
          Use this as a helper when you want a summary across authorized clients.
          It is not the main investigation workspace and it does not run
          diagnostics or remediation. For active work, open a client and then the
          incident cockpit.
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

import { ChatSessionPanel } from "./ChatSessionPanel";

type IncidentChatPanelProps = {
  clientId: string;
  fabricId: string;
  incidentId: string;
};

export function IncidentChatPanel({
  clientId,
  fabricId,
  incidentId,
}: IncidentChatPanelProps) {
  return (
    <ChatSessionPanel
      createPayload={{
        scope: "incident",
        client_id: clientId,
        fabric_id: fabricId,
        incident_id: incidentId,
      }}
      description="This incident chat shell is persisted in the Phase 1 chat tables and follows incident scope checks."
      emptyStateMessage="No incident chat session has been created for this cockpit yet."
      placeholder="Ask for more evidence, diagnostics, or next investigative steps."
      storageKey={`incident:${incidentId}`}
      title="Incident Chat Session"
    />
  );
}

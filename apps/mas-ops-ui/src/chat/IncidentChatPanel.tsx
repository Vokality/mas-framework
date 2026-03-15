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
      description="This incident chat path runs fabric-local orchestration, read-only diagnostics, evidence-backed summary updates, and approval-gated write actions."
      emptyStateMessage="No incident chat session has been created for this cockpit yet."
      placeholder="Ask for evidence, diagnostics, or propose a remediation that may require approval."
      storageKey={`incident:${incidentId}`}
      title="Incident Chat Session"
    />
  );
}

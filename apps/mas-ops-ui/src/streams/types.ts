export type StreamEventPayload = {
  event_id: string;
  client_id: string | null;
  occurred_at: string;
  subject_type: string;
  subject_id: string;
  payload: Record<string, unknown>;
};

export type StreamFrame = {
  id: string | null;
  event: string;
  retry: number | null;
  data: StreamEventPayload;
};

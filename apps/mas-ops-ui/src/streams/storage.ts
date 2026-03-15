const STREAM_LAST_EVENT_ID_PREFIX = "mas-ops-stream:last-event-id:";

export function readLastEventId(scopeKey: string): string | null {
  try {
    return window.sessionStorage.getItem(
      `${STREAM_LAST_EVENT_ID_PREFIX}${scopeKey}`,
    );
  } catch {
    return null;
  }
}

export function writeLastEventId(scopeKey: string, eventId: string | null): void {
  if (!eventId) {
    return;
  }
  try {
    window.sessionStorage.setItem(
      `${STREAM_LAST_EVENT_ID_PREFIX}${scopeKey}`,
      eventId,
    );
  } catch {
    // Ignore storage failures so stream consumption still works.
  }
}

export function clearLastEventId(scopeKey: string): void {
  try {
    window.sessionStorage.removeItem(`${STREAM_LAST_EVENT_ID_PREFIX}${scopeKey}`);
  } catch {
    // Ignore storage failures so scope loss still unwinds locally.
  }
}

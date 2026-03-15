const CHAT_SESSION_STORAGE_PREFIX = "mas-ops-chat:session-id:";

export function readChatSessionId(storageKey: string): string | null {
  try {
    return window.sessionStorage.getItem(`${CHAT_SESSION_STORAGE_PREFIX}${storageKey}`);
  } catch {
    return null;
  }
}

export function writeChatSessionId(storageKey: string, chatSessionId: string): void {
  try {
    window.sessionStorage.setItem(
      `${CHAT_SESSION_STORAGE_PREFIX}${storageKey}`,
      chatSessionId,
    );
  } catch {
    // Ignore storage failures so chat still functions within the session.
  }
}

export function clearChatSessionId(storageKey: string): void {
  try {
    window.sessionStorage.removeItem(`${CHAT_SESSION_STORAGE_PREFIX}${storageKey}`);
  } catch {
    // Ignore storage failures so chat teardown still completes locally.
  }
}

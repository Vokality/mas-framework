const LAST_CLIENT_STORAGE_KEY = "mas-ops-ui:last-client-id";

export function readLastViewedClientId(): string | null {
  return window.sessionStorage.getItem(LAST_CLIENT_STORAGE_KEY);
}

export function writeLastViewedClientId(clientId: string): void {
  window.sessionStorage.setItem(LAST_CLIENT_STORAGE_KEY, clientId);
}

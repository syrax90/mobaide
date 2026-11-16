/**
 * The name of this browser tab as the controller of a Herdr pane.
 *
 * Control of a pane belongs to one client. The agent puts the controller's id
 * in the pane's state frame, and every other client keeps the dictated grid and
 * sends no size. One id for the life of the tab: a reload is a new client.
 */
function newClientId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  // An insecure context has no `randomUUID`. The id is a name, not a secret.
  return `tab-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

export const PANE_CLIENT_ID = newClientId();

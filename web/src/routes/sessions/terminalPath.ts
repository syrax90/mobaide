/**
 * The terminal route's URL.
 *
 * Both ids are percent-encoded: they are server-generated, but a `/` in either
 * would otherwise silently retarget the navigation at a different route.
 */
export function terminalPath(agentId: string, sessionId: string): string {
  return `/agents/${encodeURIComponent(agentId)}/sessions/${encodeURIComponent(sessionId)}`;
}

/** The create-wizard route for an agent. */
export function newSessionPath(agentId: string): string {
  return `/agents/${encodeURIComponent(agentId)}/new-session`;
}

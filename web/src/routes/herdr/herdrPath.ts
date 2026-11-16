/**
 * The Herdr routes. A pane is named by server and pane id in the query, not in
 * the path: a pane id contains `:` and can contain `/`.
 */
export function herdrPath(agentId: string): string {
  return `/agents/${encodeURIComponent(agentId)}/herdr`;
}

export function herdrPanePath(agentId: string, serverId: string, paneId: string): string {
  const query = new URLSearchParams({ server: serverId, pane: paneId });
  return `${herdrPath(agentId)}/pane?${query.toString()}`;
}

/** The key one pane has in the visible-session store and as a notification tag. */
export function herdrPaneTag(agentId: string, serverId: string, paneId: string): string {
  return `herdr\u0000${agentId}\u0000${serverId}\u0000${paneId}`;
}

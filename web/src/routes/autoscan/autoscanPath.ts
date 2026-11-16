/**
 * The Auto Scan monitor's URL.
 *
 * Agent- and workspace-scoped, so both ride in the path rather than resting on the
 * selected-agent store: a scan is only meaningful for one workspace on one host,
 * and a shared link would otherwise resolve somewhere else. Same argument the
 * git route makes.
 *
 * A scan is identified by its workspace name, not by an id — the agent keys
 * `AutoScanSessionManager._scans` by name — and a workspace name is user-supplied,
 * so encoding it is what keeps a `/` in one from retargeting the navigation.
 */
export function autoScanDetailPath(agentId: string, workspaceName: string): string {
  return `/agents/${encodeURIComponent(agentId)}/autoscan/${encodeURIComponent(workspaceName)}`;
}

/**
 * The Auto Scan monitor's URL.
 *
 * Agent- and project-scoped, so both ride in the path rather than resting on the
 * selected-agent store: a scan is only meaningful for one project on one host,
 * and a shared link would otherwise resolve somewhere else. Same argument the
 * git route makes.
 *
 * A scan is identified by its project name, not by an id — the agent keys
 * `AutoScanSessionManager._scans` by name — and a project name is user-supplied,
 * so encoding it is what keeps a `/` in one from retargeting the navigation.
 */
export function autoScanDetailPath(agentId: string, projectName: string): string {
  return `/agents/${encodeURIComponent(agentId)}/autoscan/${encodeURIComponent(projectName)}`;
}

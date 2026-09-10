/**
 * The one place query keys are spelled.
 *
 * Before WEB-04 each key was a module-local tuple next to its only consumer,
 * which worked because nothing else needed to name them. The WS event bridge
 * changes that: it invalidates lists it does not own, from outside React, so a
 * typo would fail silently as a no-op invalidation.
 *
 * Keys are account-agnostic by design — `src/auth/install.ts` drops the whole
 * cache when the signed-in user changes, rather than scoping every key.
 * Agent-scoped keys are prefixed so `['agent', id]` invalidates everything
 * belonging to one agent.
 */

/** What identifies one diff request; the object form keeps the key readable. */
export interface SessionGitDiffKey {
  path: string;
  scope: string;
  context: number;
  origPath?: string;
}

export const queryKeys = {
  serverInfo: () => ['server-info'] as const,
  devices: () => ['devices'] as const,
  agents: () => ['agents'] as const,
  templates: () => ['templates'] as const,
  customAgentTypes: () => ['custom-agent-types'] as const,
  /**
   * One in-flight device-code pairing request.
   *
   * Polled rather than pushed: pairing is a short foreground handshake with no
   * entity behind it to carry a `*.changed` event, and the poll stops as soon
   * as the request settles.
   */
  devicePairing: (requestId: string) => ['device-pairing', requestId] as const,
  /** The relay's signing fingerprint and this account's recorded pin. */
  trustPin: () => ['trust-pin'] as const,

  agent: (agentId: string) => ['agent', agentId] as const,
  sessions: (agentId: string) => ['agent', agentId, 'sessions'] as const,
  projects: (agentId: string) => ['agent', agentId, 'projects'] as const,
  /**
   * One project's branches and worktrees. The prefix `['agent', id, 'git']`
   * invalidates every project's list at once, which is what a reconnect needs:
   * `git.changed` only fires on a change, so a socket that was down leaves each
   * cached list unverifiable.
   */
  gitItems: (agentId: string, projectName: string) =>
    ['agent', agentId, 'git', projectName] as const,
  gitItemsRoot: (agentId: string) => ['agent', agentId, 'git'] as const,
  /**
   * Linear labels and states for one project's Auto Scan filter.
   *
   * Deliberately **not** nested under `projects`: query keys match by prefix, so
   * a key like `['agent', id, 'projects', name, 'linear']` would make every
   * project-list invalidation re-hit the rate-limited Linear API.
   */
  linearMetadata: (agentId: string, projectName: string) =>
    ['agent', agentId, 'linear', projectName] as const,
  /**
   * The in-session git Changes viewer: one session's working-tree status and
   * its per-file diffs.
   *
   * Its own segment, not `gitItems`' `['agent', id, 'git', …]`: a session id in
   * the slot a project name occupies would prefix-collide, and the project
   * lists' reconnect sweep and `git.changed` invalidations would refetch every
   * open viewer's poll.
   */
  sessionGitRoot: (agentId: string) => ['agent', agentId, 'session-git'] as const,
  sessionGit: (agentId: string, sessionId: string) =>
    ['agent', agentId, 'session-git', sessionId] as const,
  sessionGitStatus: (agentId: string, sessionId: string) =>
    ['agent', agentId, 'session-git', sessionId, 'status'] as const,
  /** Every diff of one session — the prefix a moved working tree invalidates. */
  sessionGitDiffs: (agentId: string, sessionId: string) =>
    ['agent', agentId, 'session-git', sessionId, 'diff'] as const,
  sessionGitDiff: (agentId: string, sessionId: string, request: SessionGitDiffKey) =>
    ['agent', agentId, 'session-git', sessionId, 'diff', request] as const,
  autoscan: (agentId: string) => ['agent', agentId, 'autoscan'] as const,
  /** Which CLIs the agent has installed, and the models each advertises. */
  agentCapabilities: (agentId: string) => ['agent', agentId, 'capabilities'] as const,
};

/**
 * Options for a list the server pushes over WebSocket.
 *
 * Such a list is only ever stale because an event said so, so time-based
 * staleness is pure noise. This is opt-in rather than a global default: the
 * queries that exist today (`server-info`, `devices`) have no push channel, and
 * an `Infinity` default would freeze them.
 */
export const wsBackedQueryOptions = {
  staleTime: Infinity,
} as const;

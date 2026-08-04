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
export const queryKeys = {
  serverInfo: () => ['server-info'] as const,
  devices: () => ['devices'] as const,
  agents: () => ['agents'] as const,
  templates: () => ['templates'] as const,
  shortcuts: () => ['shortcuts'] as const,
  customAgentTypes: () => ['custom-agent-types'] as const,

  agent: (agentId: string) => ['agent', agentId] as const,
  sessions: (agentId: string) => ['agent', agentId, 'sessions'] as const,
  projects: (agentId: string) => ['agent', agentId, 'projects'] as const,
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

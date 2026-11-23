/**
 * `agent_settings.changed`, applied straight to the query cache.
 *
 * The payload is the agent's whole `AgentSettingsResponse` — the same body
 * `GET /api/agent/settings` answers — published on every write, when
 * keep-awake loses or regains its lock by itself, and again on every
 * events-channel connect. A snapshot, so it is written, not invalidated.
 */
import type { QueryClient } from '@tanstack/react-query';
import { queryKeys } from '../lib/queryKeys';
import { agentSettingsSchema, type AgentSettings } from '../protocol/dto/agentSettings';

export function applyAgentSettingsSnapshot(
  client: QueryClient,
  agentId: string,
  payload: unknown,
): 'applied' | 'dropped' {
  const decoded = agentSettingsSchema.safeParse(payload);
  if (!decoded.success) return 'dropped';
  const key = queryKeys.agentSettings(agentId);
  // A GET started before this frame could resolve after it with an older state.
  void client.cancelQueries({ queryKey: key });
  client.setQueryData<AgentSettings>(key, decoded.data);
  return 'applied';
}

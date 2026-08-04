import { relayClient } from './clients';
import { toApiError } from './errors';

export interface Agent {
  agentId: string;
  name: string;
  online: boolean;
  createdAt: string;
  lastSeenAt: string | null;
  agentVersion: string | null;
  agentProtocolVersion: number | null;
}

/**
 * Online agents first, then by name.
 *
 * Mobile has no equivalent — one install is bound to one agent — so the order
 * is a web decision: an offline agent's session list is unreachable, which
 * makes it the wrong thing to default a fresh browser to.
 */
function byOnlineThenName(a: Agent, b: Agent): number {
  if (a.online !== b.online) return a.online ? -1 : 1;
  return a.name.toLowerCase().localeCompare(b.name.toLowerCase());
}

export async function listAgents(): Promise<Agent[]> {
  const { data, error, response } = await relayClient.GET('/api/agents');
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return data
    .map((agent) => ({
      agentId: agent.agent_id,
      name: agent.name,
      online: agent.online,
      createdAt: agent.created_at,
      lastSeenAt: agent.last_seen_at,
      agentVersion: agent.agent_version ?? null,
      agentProtocolVersion: agent.agent_protocol_version ?? null,
    }))
    .sort(byOnlineThenName);
}

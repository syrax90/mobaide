import { useQuery } from '@tanstack/react-query';
import { fetchAgentCapabilities } from '../api/agentCapabilities';
import { fetchHerdr } from '../api/herdrRepository';
import { queryKeys, wsBackedQueryOptions } from '../lib/queryKeys';
import { herdrCapability } from '../protocol/dto/agentVersion';

const CAPABILITIES_STALE_MS = 5 * 60_000;

/** `capabilities.herdr.dashboard`, from the cached `/api/version` probe. */
export function useHerdrCapability(agentId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.agentCapabilities(agentId),
    queryFn: () => fetchAgentCapabilities(agentId),
    enabled,
    staleTime: CAPABILITIES_STALE_MS,
    select: herdrCapability,
    meta: { silent: true },
  });
}

/**
 * The agent's Herdr servers, or nothing for an agent without the dashboard.
 *
 * Kept fresh by `herdr.changed`, which needs the agent events channel open:
 * every screen that calls this opens it with `useAgentEvents`. The agent sends
 * the whole state again when that channel reconnects, so the entry is stale
 * only when an event says so.
 */
export function useHerdr(agentId: string, online: boolean) {
  const capability = useHerdrCapability(agentId, online);
  const supported = capability.data === true;
  const snapshot = useQuery({
    queryKey: queryKeys.herdr(agentId),
    queryFn: () => fetchHerdr(agentId),
    enabled: online && supported,
    meta: { silent: true },
    ...wsBackedQueryOptions,
  });
  return { supported, snapshot };
}

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchAgentCapabilities } from '../../api/agentCapabilities';
import { getAgentSettings, setKeepAwake } from '../../api/agentSettingsRepository';
import { queryKeys, wsBackedQueryOptions } from '../../lib/queryKeys';
import type { AgentSettings } from '../../protocol/dto/agentSettings';
import { keepAwakeCapability } from '../../protocol/dto/agentVersion';
import { unreachablePoll } from '../../voice/useVoiceStatus';

/** Re-probed no more often than this: a capability only moves on an agent update. */
const CAPABILITIES_STALE_MS = 5 * 60_000;

/** `capabilities.agent_settings.keep_awake`, from the cached `/api/version` probe. */
export function useKeepAwakeCapability(agentId: string) {
  return useQuery({
    queryKey: queryKeys.agentCapabilities(agentId),
    queryFn: () => fetchAgentCapabilities(agentId),
    staleTime: CAPABILITIES_STALE_MS,
    select: keepAwakeCapability,
    // Nothing tells a failed probe that the agent is back: ask again by itself.
    refetchInterval: (query) => unreachablePoll(query.state),
    meta: { silent: true },
  });
}

/**
 * The agent's settings. Kept fresh by `agent_settings.changed`, which needs
 * the agent events channel open (`useAgentEvents`).
 */
export function useAgentSettings(agentId: string, enabled: boolean) {
  return useQuery({
    ...wsBackedQueryOptions,
    queryKey: queryKeys.agentSettings(agentId),
    queryFn: ({ signal }) => getAgentSettings(agentId, signal),
    enabled,
    refetchInterval: (query) => unreachablePoll(query.state),
    meta: { silent: true },
  });
}

/**
 * Turns keep-awake on or off on the agent, for every client of it. The
 * agent answers with the applied state; a snapshot that arrived while the
 * request was in flight is newer and wins over the answer.
 */
export function useKeepAwakeSetting(agentId: string) {
  const queryClient = useQueryClient();
  const key = queryKeys.agentSettings(agentId);
  return useMutation({
    mutationFn: (enabled: boolean) => setKeepAwake(agentId, enabled),
    onMutate: async () => {
      await queryClient.cancelQueries({ queryKey: key });
      return { before: queryClient.getQueryData<AgentSettings>(key) };
    },
    onSuccess: (next, _enabled, context) => {
      if (queryClient.getQueryData<AgentSettings>(key) === context?.before) {
        queryClient.setQueryData<AgentSettings>(key, next);
      }
    },
    meta: { silent: true },
  });
}

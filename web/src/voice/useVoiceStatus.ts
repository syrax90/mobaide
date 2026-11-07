import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchAgentCapabilities } from '../api/agentCapabilities';
import {
  cancelVoiceInstall,
  getVoiceStatus,
  installVoice,
  removeVoice,
  setVoiceCleanup,
  toVoiceApiError,
} from '../api/voiceRepository';
import { queryKeys, wsBackedQueryOptions } from '../lib/queryKeys';
import { voiceCapability, voiceCleanupCapability } from '../protocol/dto/agentVersion';
import { VOICE_STATE, type VoiceStatus } from '../protocol/dto/voice';

/** Re-probed no more often than this: a capability only moves on an agent update. */
const CAPABILITIES_STALE_MS = 5 * 60_000;

/**
 * While another process holds the install (`queued`), agents that predate
 * the lock watcher publish nothing for it, so the status is polled.
 */
export const VOICE_QUEUED_POLL_MS = 4_000;

/** `capabilities.voice`, sharing the cached `/api/version` probe with the git menu. */
export function useVoiceCapability(agentId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.agentCapabilities(agentId),
    queryFn: () => fetchAgentCapabilities(agentId),
    enabled,
    staleTime: CAPABILITIES_STALE_MS,
    select: voiceCapability,
    meta: { silent: true },
  });
}

/** `capabilities.voice.cleanup`, from the same cached probe. */
export function useVoiceCleanupCapability(agentId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.agentCapabilities(agentId),
    queryFn: () => fetchAgentCapabilities(agentId),
    enabled,
    staleTime: CAPABILITIES_STALE_MS,
    select: voiceCleanupCapability,
    meta: { silent: true },
  });
}

/**
 * The agent's voice status. Kept fresh by `voice.changed`, which needs the
 * agent events channel open: the terminal holds it already, other callers
 * open it with `useAgentEvents`.
 */
export function useVoiceStatus(agentId: string, enabled: boolean) {
  return useQuery({
    ...wsBackedQueryOptions,
    queryKey: queryKeys.voiceStatus(agentId),
    queryFn: ({ signal }) => getVoiceStatus(agentId, signal),
    enabled,
    refetchInterval: (query) =>
      query.state.data?.state === VOICE_STATE.queued ? VOICE_QUEUED_POLL_MS : false,
    meta: { silent: true },
  });
}

export type VoiceAction = 'install' | 'cancel' | 'remove' | 'reinstall';

/** Install / cancel / remove; each answers the new status, written to the cache. */
export function useVoiceActions(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (action: VoiceAction): Promise<VoiceStatus> => {
      try {
        switch (action) {
          case 'install':
            return await installVoice(agentId);
          case 'cancel':
            return await cancelVoiceInstall(agentId);
          case 'remove':
            return await removeVoice(agentId);
          case 'reinstall':
            await removeVoice(agentId);
            return await installVoice(agentId);
        }
      } catch (error) {
        throw toVoiceApiError(error);
      }
    },
    onSuccess: (status) => {
      queryClient.setQueryData<VoiceStatus>(queryKeys.voiceStatus(agentId), status);
    },
    meta: { silent: true },
  });
}

/**
 * The agent's transcript-cleanup choice, stored on the agent so every client
 * of it shares one setting. The cached status changes at once and goes back
 * if the agent refuses, unless a newer snapshot replaced it meanwhile;
 * `voice.changed` brings changes from other clients.
 */
export function useVoiceCleanupSetting(agentId: string, status: VoiceStatus | undefined) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: async (enabled: boolean): Promise<VoiceStatus> => {
      try {
        return await setVoiceCleanup(agentId, enabled);
      } catch (error) {
        throw toVoiceApiError(error);
      }
    },
    onMutate: async (enabled) => {
      const key = queryKeys.voiceStatus(agentId);
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<VoiceStatus>(key);
      if (previous === undefined) return undefined;
      // The stored object, not the argument: structural sharing copies it.
      const optimistic = queryClient.setQueryData<VoiceStatus>(key, {
        ...previous,
        cleanup_enabled: enabled,
      });
      return { previous, optimistic };
    },
    onError: (_error, _enabled, context) => {
      // A `voice.changed` that arrived meanwhile is newer than both: keep it.
      const key = queryKeys.voiceStatus(agentId);
      if (context !== undefined && queryClient.getQueryData<VoiceStatus>(key) === context.optimistic) {
        queryClient.setQueryData<VoiceStatus>(key, context.previous);
      }
    },
    onSuccess: (next) => {
      queryClient.setQueryData<VoiceStatus>(queryKeys.voiceStatus(agentId), next);
    },
    meta: { silent: true },
  });
  return {
    on: status?.cleanup_enabled === true,
    set: mutation.mutate,
    failed: mutation.isError,
  };
}

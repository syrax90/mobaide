import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getCloudVoice, setVoiceEngine } from '../api/cloudVoiceRepository';
import { toVoiceApiError } from '../api/voiceRepository';
import { queryKeys } from '../lib/queryKeys';
import { CLOUD_VOICE_STATE, type CloudVoice, type VoiceEngine } from '../protocol/dto/cloudVoice';

const STALE_MS = 60_000;
/** While it is down and a screen shows it, ask again by itself. */
const UNAVAILABLE_POLL_MS = 30_000;

/**
 * Cloud voice input for the account. `cloud_voice.changed` refreshes it when
 * another device changes an engine choice; a failed dictation refreshes it
 * too, so the mic shows the real state.
 */
export function useCloudVoice() {
  return useQuery({
    queryKey: queryKeys.cloudVoice(),
    queryFn: ({ signal }) => getCloudVoice(signal),
    staleTime: STALE_MS,
    refetchInterval: (query) =>
      query.state.data?.state === CLOUD_VOICE_STATE.unavailable ? UNAVAILABLE_POLL_MS : false,
    meta: { silent: true },
  });
}

/**
 * The engine choice of one agent, stored on the server for every device. The
 * cache changes at once and goes back if the server refuses.
 */
export function useVoiceEngineChoice(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    // One at a time per agent: two quick choices reach the server in order.
    scope: { id: `voice-engine:${agentId}` },
    mutationFn: async (engine: VoiceEngine): Promise<CloudVoice> => {
      try {
        return await setVoiceEngine(agentId, engine);
      } catch (error) {
        throw toVoiceApiError(error);
      }
    },
    onMutate: async (engine) => {
      const key = queryKeys.cloudVoice();
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<CloudVoice>(key);
      if (previous === undefined) return undefined;
      const engines = new Map(Object.entries(previous.engines));
      engines.set(agentId, engine);
      queryClient.setQueryData<CloudVoice>(key, {
        ...previous,
        engines: Object.fromEntries(engines),
      });
      return { previous };
    },
    onError: (_error, _engine, context) => {
      if (context !== undefined) {
        queryClient.setQueryData<CloudVoice>(queryKeys.cloudVoice(), context.previous);
      }
    },
    onSuccess: (next) => {
      queryClient.setQueryData<CloudVoice>(queryKeys.cloudVoice(), next);
    },
    meta: { silent: true },
  });
}

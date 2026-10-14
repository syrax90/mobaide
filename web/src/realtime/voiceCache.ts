/**
 * `voice.changed`, applied straight to the query cache.
 *
 * The payload is the agent's whole `VoiceStatusResponse` — the same body
 * `GET /api/voice/status` answers — published on every state change, throttled
 * to two a second while a download runs, and again on every events-channel
 * connect. A snapshot, so it is written (the `projectsCache.ts` rule) rather
 * than invalidated: an invalidation per progress frame would be a `/proxy`
 * GET per frame.
 */
import type { QueryClient } from '@tanstack/react-query';
import { queryKeys } from '../lib/queryKeys';
import { voiceStatusSchema, type VoiceStatus } from '../protocol/dto/voice';

export function applyVoiceSnapshot(
  client: QueryClient,
  agentId: string,
  payload: unknown,
): 'applied' | 'dropped' {
  const decoded = voiceStatusSchema.safeParse(payload);
  if (!decoded.success) return 'dropped';
  const key = queryKeys.voiceStatus(agentId);
  // A GET started before this frame could resolve after it with an older state.
  void client.cancelQueries({ queryKey: key });
  client.setQueryData<VoiceStatus>(key, decoded.data);
  return 'applied';
}

/**
 * `herdr.changed`, applied straight to the query cache.
 *
 * The event carries the agent's whole Herdr state, so it is written rather
 * than used to invalidate — the same exception `autoscanCache.ts` documents.
 * The agent also sends it when the events socket opens, so the dashboard needs
 * no refetch after a reconnect.
 */
import type { QueryClient } from '@tanstack/react-query';
import { queryKeys } from '../lib/queryKeys';
import { herdrSnapshotSchema, type HerdrSnapshot } from '../protocol/dto/herdr';

export function applyHerdrSnapshot(
  client: QueryClient,
  agentId: string,
  payload: unknown,
): 'applied' | 'dropped' {
  // Every field of the schema has a default, so any object would parse to an
  // empty dashboard. Require the array before trusting the payload.
  if (
    typeof payload !== 'object' ||
    payload === null ||
    !Array.isArray((payload as { servers?: unknown }).servers)
  ) {
    return 'dropped';
  }
  const decoded = herdrSnapshotSchema.safeParse(payload);
  if (!decoded.success) return 'dropped';
  const key = queryKeys.herdr(agentId);
  // A slower `GET /api/herdr` must not overwrite this newer state.
  void client.cancelQueries({ queryKey: key });
  client.setQueryData<HerdrSnapshot>(key, decoded.data);
  return 'applied';
}

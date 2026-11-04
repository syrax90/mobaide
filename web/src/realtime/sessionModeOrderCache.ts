/**
 * The account's mode-picker order, written straight into the query cache.
 *
 * `session_mode_order.updated` carries the whole order, so it is written (the
 * `projectsCache.ts` rule) rather than invalidated. The PUT response carries
 * the same body. Both go through `writeSessionModeOrder`, which keeps the
 * newer of the cached and the incoming value by `version`: a push and the
 * response to the same save can land in either order.
 */
import type { QueryClient } from '@tanstack/react-query';
import { queryKeys } from '../lib/queryKeys';
import { sessionModeOrderSchema, type SessionModeOrder } from '../protocol/dto/userEvents';

/** The newer of two snapshots; a tie keeps `incoming`. */
export function newerSessionModeOrder(
  current: SessionModeOrder | undefined,
  incoming: SessionModeOrder,
): SessionModeOrder {
  return current !== undefined && current.version > incoming.version ? current : incoming;
}

/** Returns whether the cache now holds `next`. */
export function writeSessionModeOrder(client: QueryClient, next: SessionModeOrder): boolean {
  let written = false;
  client.setQueryData<SessionModeOrder>(queryKeys.sessionModeOrder(), (current) => {
    const kept = newerSessionModeOrder(current, next);
    written = kept === next;
    return kept;
  });
  return written;
}

export function applySessionModeOrderEvent(
  client: QueryClient,
  payload: unknown,
): 'applied' | 'stale' | 'dropped' {
  const decoded = sessionModeOrderSchema.safeParse(payload);
  if (!decoded.success) return 'dropped';
  return writeSessionModeOrder(client, decoded.data) ? 'applied' : 'stale';
}

/**
 * The three agent-channel session events, applied straight to the query cache.
 *
 * These are the exception `queryBridge`'s "invalidate, never patch" policy
 * carves out for full-snapshot events, and they qualify on the wire:
 *
 * * `sessions.changed` is the agent's **complete** list, rebuilt from
 *   `list_sessions()` and re-pushed on every connect
 *   (`core/session_manager.py::_publish_sessions_changed`).
 * * `session.updated` and the agent-channel `session.attention` carry a
 *   **whole** session row, not a partial patch (`agent/ws/event_fanout.py`).
 *
 * There is therefore no delta to drift. Invalidating instead would cost a
 * `/proxy` round trip for every title change an agent emits, and would let a
 * slow REST response land after a newer snapshot and roll the list backwards.
 * Both mobile repositories patch for exactly these reasons.
 */
import type { QueryClient } from '@tanstack/react-query';
import { queryKeys } from '../lib/queryKeys';
import {
  sessionEventSchema,
  sessionsChangedSchema,
  type SessionResponse,
} from '../protocol/dto/session';
import { evictTerminals } from '../terminal/terminalEviction';

/** Replace the row with this `session_id`, or append it. iOS `SessionRepository.upsert`. */
export function upsertSession(
  list: readonly SessionResponse[],
  session: SessionResponse,
): SessionResponse[] {
  const index = list.findIndex((entry) => entry.session_id === session.session_id);
  if (index === -1) return [...list, session];
  const next = [...list];
  next[index] = session;
  return next;
}

/** The ids in `previous` that the authoritative `next` list no longer contains. */
export function removedSessionIds(
  previous: readonly SessionResponse[],
  next: readonly SessionResponse[],
): string[] {
  const surviving = new Set(next.map((session) => session.session_id));
  return previous
    .filter((session) => !surviving.has(session.session_id))
    .map((session) => session.session_id);
}

/**
 * `sessions.changed`. **May seed** a cache entry nothing has fetched: the
 * payload is authoritative, so a list mounting a moment later can paint from it
 * with no request at all. (That is why the sessions query must not set
 * `refetchOnMount: 'always'` — it would throw the seed away.)
 */
export function applySessionsSnapshot(
  client: QueryClient,
  agentId: string,
  payload: unknown,
): 'applied' | 'dropped' {
  const decoded = sessionsChangedSchema.safeParse(payload);
  if (!decoded.success) return 'dropped';
  const sessions = decoded.data.sessions;
  const key = queryKeys.sessions(agentId);

  // Diff before the write, and only against a cache that already existed — a
  // seed has nothing to remove. Removal-driven eviction is only sound because
  // this event is a complete list; an upsert must never route through here.
  const previous = client.getQueryData<SessionResponse[]>(key);
  if (previous !== undefined) {
    evictTerminals(agentId, removedSessionIds(previous, sessions));
  }

  client.setQueryData<SessionResponse[]>(key, () => sessions);
  return 'applied';
}

/**
 * `session.updated` and the agent-channel `session.attention`.
 *
 * **Must never seed.** A single row written into an empty cache would look like
 * the whole list — one session where the agent may be running twelve. TanStack
 * skips creating the entry when the updater returns `undefined`, which is the
 * mechanism relied on here, so `'skipped'` is a normal outcome rather than a
 * failure: the list simply is not mounted yet, and the connect-time snapshot
 * will bring the truth.
 */
export function applySessionUpsert(
  client: QueryClient,
  agentId: string,
  payload: unknown,
): 'applied' | 'skipped' | 'dropped' {
  const decoded = sessionEventSchema.safeParse(payload);
  if (!decoded.success) return 'dropped';

  let applied = false;
  client.setQueryData<SessionResponse[]>(queryKeys.sessions(agentId), (previous) => {
    if (previous === undefined) return undefined;
    applied = true;
    return upsertSession(previous, decoded.data.session);
  });
  return applied ? 'applied' : 'skipped';
}

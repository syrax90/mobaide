/**
 * The session's kind, or null while it cannot be determined.
 *
 * Saved shortcuts are filed per kind, and filing one under a guessed kind puts
 * it in the wrong bucket permanently — unlike the device-local recents, which
 * self-heal. So the value is a one-way latch, exactly like Android's
 * `resolvedKind.compareAndSet(null, kind)` and iOS's `adoptResolvedKind`: once
 * the agent has told us, a session later vanishing from the snapshot (it ended,
 * and the terminal deliberately stays readable) must not silently re-render the
 * bar as the neutral set.
 *
 * The query is the same one `useSessionGone` runs; TanStack dedupes on the key,
 * so mounting both costs one fetch.
 */
import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { listSessions } from '../../api/sessionRepository';
import { queryKeys, wsBackedQueryOptions } from '../../lib/queryKeys';
import { useSessionsSynced } from '../../realtime';

export function useSessionKind(agentId: string, sessionId: string): string | null {
  const synced = useSessionsSynced(agentId);
  const sessions = useQuery({
    queryKey: queryKeys.sessions(agentId),
    queryFn: () => listSessions(agentId),
    // The relay 503s for an unreachable agent, and the global query-error toast
    // would fire on it.
    enabled: synced,
    ...wsBackedQueryOptions,
  });

  const listed =
    sessions.data?.find((session) => session.session_id === sessionId)?.kind ?? null;
  const [resolved, setResolved] = useState<string | null>(null);

  useEffect(() => {
    if (listed !== null && resolved === null) setResolved(listed);
  }, [listed, resolved]);

  return resolved ?? listed;
}

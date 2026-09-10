/**
 * The terminal route's own session row, or `undefined` until the list resolves
 * (or once the session has left it).
 *
 * The sessions query is the one `useSessionGone` already runs; TanStack dedupes
 * on the key, so every consumer on this route shares one fetch. Gated on
 * `synced` for the same reason as there: the relay 503s for an unreachable
 * agent, and the global query-error toast would fire on it.
 */
import { useQuery } from '@tanstack/react-query';
import { listSessions } from '../../api/sessionRepository';
import { queryKeys, wsBackedQueryOptions } from '../../lib/queryKeys';
import type { SessionResponse } from '../../protocol/dto/session';
import { useSessionsSynced } from '../../realtime';

export function useSessionRow(agentId: string, sessionId: string): SessionResponse | undefined {
  const synced = useSessionsSynced(agentId);
  const sessions = useQuery({
    queryKey: queryKeys.sessions(agentId),
    queryFn: () => listSessions(agentId),
    enabled: synced,
    ...wsBackedQueryOptions,
  });
  return sessions.data?.find((row) => row.session_id === sessionId);
}

/**
 * What the terminal bar names this session: its live OSC title while there is
 * one, else the kind label. Empty only until the session row resolves.
 *
 * Not a one-way latch: the value must keep changing — the agent repoints the
 * title as the CLI works. It only refuses to go *back* to empty, so a session
 * vanishing from the snapshot (it ended, and the terminal deliberately stays
 * readable) does not blank the bar.
 *
 * What it retains is stamped with the route it was resolved for. A notification
 * tap navigates terminal→terminal, which swaps this route's params without
 * remounting, and the incoming session's row is not in the cache yet — an
 * unstamped value would name the new terminal after the old one, indefinitely
 * if that agent never syncs.
 *
 * The sessions query is the one `useSessionGone` already runs; TanStack
 * dedupes on the key, so this costs no extra fetch.
 */
import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { listCustomAgentTypes } from '../../api/customAgentTypeRepository';
import { listSessions } from '../../api/sessionRepository';
import { queryKeys, wsBackedQueryOptions } from '../../lib/queryKeys';
import { useSessionsSynced } from '../../realtime';
import { customTypeNameMap, sessionHeaderLabel } from '../sessions/sessionDisplay';

export function useSessionHeaderLabel(agentId: string, sessionId: string): string {
  const synced = useSessionsSynced(agentId);
  const sessions = useQuery({
    queryKey: queryKeys.sessions(agentId),
    queryFn: () => listSessions(agentId),
    // The relay 503s for an unreachable agent, and the global query-error toast
    // would fire on it.
    enabled: synced,
    ...wsBackedQueryOptions,
  });
  // Gated on the same flag: the registry is only ever consulted for a session
  // row, so fetching it before one can resolve buys nothing.
  const customTypes = useQuery({
    queryKey: queryKeys.customAgentTypes(),
    queryFn: listCustomAgentTypes,
    enabled: synced,
    ...wsBackedQueryOptions,
  });

  const session = sessions.data?.find((row) => row.session_id === sessionId);
  const label =
    session === undefined
      ? ''
      : sessionHeaderLabel(session, customTypeNameMap(customTypes.data));
  const route = `${agentId}/${sessionId}`;
  const [retained, setRetained] = useState({ route, label: '' });

  useEffect(() => {
    if (label !== '') setRetained({ route, label });
  }, [route, label]);

  if (label !== '') return label;
  // Compared during render, not reset by the effect, so the very first frame
  // after a route swap is already blank rather than briefly stale.
  return retained.route === route ? retained.label : '';
}

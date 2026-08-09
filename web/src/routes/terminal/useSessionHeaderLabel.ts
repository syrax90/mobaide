/**
 * What the terminal bar names this session: its live OSC title while there is
 * one, else the kind label. Empty only until the session row resolves.
 *
 * Unlike `useSessionKind`'s one-way latch, the value must keep changing — the
 * agent repoints the title as the CLI works. It only refuses to go *back* to
 * empty, so a session vanishing from the snapshot (it ended, and the terminal
 * deliberately stays readable) does not blank the bar.
 *
 * The sessions query is the one `useSessionGone` and `useSessionKind` already
 * run; TanStack dedupes on the key, so this costs no extra fetch.
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
  const [lastLabel, setLastLabel] = useState('');

  useEffect(() => {
    if (label !== '') setLastLabel(label);
  }, [label]);

  return label === '' ? lastLabel : label;
}

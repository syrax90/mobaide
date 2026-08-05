/**
 * Leave the terminal when the agent's snapshot proves the session is gone.
 *
 * A terminal URL is trivially re-openable — a bookmark, a reload, a Back into
 * history — long after its session ended, and nothing about the route itself
 * checks that the session still exists. Mobile reaches the same state through a
 * stale auto-resume id or a restored navigation stack; all three clients answer
 * it the same way (Android `TerminalViewModel.sessionEnded`, iOS
 * `TerminalSessionModel.noteSessionSnapshot`).
 *
 * Once the session is known to exist it is never treated as missing again: a
 * session ending, or being stopped, while the user is reading it must leave the
 * output on screen rather than eject them.
 */
import { useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router';
import { listSessions } from '../../api/sessionRepository';
import { queryKeys, wsBackedQueryOptions } from '../../lib/queryKeys';
import { useAgentEvents, useSessionsSynced } from '../../realtime';
import { useToastStore } from '../../stores/toastStore';

export function useSessionGone(agentId: string, sessionId: string): void {
  const navigate = useNavigate();
  const showToast = useToastStore((state) => state.show);

  // The terminal is reachable directly (deep link, reload) with no session list
  // ever mounted, so it owns the agent channel for its own lifetime rather than
  // assuming someone else opened it. Previously the channel was closed for the
  // whole time a terminal was on screen.
  useAgentEvents(agentId);
  const synced = useSessionsSynced(agentId);

  const sessions = useQuery({
    queryKey: queryKeys.sessions(agentId),
    queryFn: () => listSessions(agentId),
    // The relay answers 503 for an unreachable agent, which the global
    // query-error toast would fire on. A connected channel is the evidence that
    // the fetch can succeed — and it gates the decision below anyway.
    enabled: synced,
    ...wsBackedQueryOptions,
  });

  const everSeenAlive = useRef(false);
  const left = useRef(false);

  // `undefined` is "no list yet", which is not the same as "not in the list" —
  // deciding on it would eject the user before the first fetch resolves.
  const loaded = sessions.data !== undefined;
  const listed = sessions.data?.some((session) => session.session_id === sessionId) ?? false;

  useEffect(() => {
    if (listed) {
      everSeenAlive.current = true;
      return;
    }
    if (!loaded || !synced || everSeenAlive.current || left.current) return;
    left.current = true;
    showToast('That session has ended.');
    void navigate('/', { replace: true });
  }, [listed, loaded, synced, navigate, showToast]);
}

/**
 * The post-claim "waiting for the agent" watcher.
 *
 * A successful claim only proves the relay persisted credentials. The agent
 * still has to receive the signed push, verify it, write its `.env` and bring
 * its relay client up — so the authoritative "paired" signal is
 * `agent.status_changed` with `online: true`, never the claim's 200 and never
 * `ws_delivered`.
 *
 * Same watcher as Android's `startWaitingForOnlineWatcher` and iOS's: the
 * push stays primary (`agent.status_changed` invalidates `['agents']` through
 * the event bridge, and an invalidation refetches this mounted query), and a
 * 5 s REST resync is the safety net for a push that never arrives — the user
 * socket down or still reconnecting when the relay emitted it, or a tab the
 * browser has parked in the background. The relay computes `online` from its
 * live routing table, so the resync is authoritative. The deadline verdict is
 * likewise read fresh: `timedOut` only reports after one terminal refetch has
 * settled with the agent still offline.
 */
import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { listAgents } from '../../api/agentRepository';
import { queryKeys, wsBackedQueryOptions } from '../../lib/queryKeys';
import { WAIT_FOR_ONLINE_TIMEOUT_SECONDS } from './pairCopy';

/**
 * Slow enough to be a safety net rather than a poll — twelve requests across
 * the whole 60 s window — and only while a wait is actually in progress.
 * Mobile's `DEFENSIVE_REFETCH_SECONDS`.
 */
const DEFENSIVE_REFETCH_MS = 5_000;

export interface WaitForOnlineState {
  online: boolean;
  elapsedSeconds: number;
  timedOut: boolean;
}

/**
 * `attempt` distinguishes one wait for an agent from the next ("Keep
 * waiting"): the clock and the one-shot refetch are keyed on it, so a restart
 * begins at zero instead of inheriting the previous attempt's elapsed count.
 */
export function useWaitForOnline(agentId: string | null, attempt = 0): WaitForOnlineState {
  const queryClient = useQueryClient();
  const waiting = agentId !== null;
  const attemptKey = agentId === null ? null : `${agentId}#${String(attempt)}`;
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [finalCheckDoneFor, setFinalCheckDoneFor] = useState<string | null>(null);

  const agents = useQuery({
    queryKey: queryKeys.agents(),
    queryFn: listAgents,
    ...wsBackedQueryOptions,
    // Gated, not merely un-intervalled. `/pair` is the app's only route that
    // renders while signed out, and a hook cannot sit behind the component's
    // `!authenticated` bail-out — so without this the query would fire an
    // authenticated `GET /api/agents` for every anonymous visitor, take a 401
    // through the session-teardown middleware, and leave an errored entry in
    // the cache that the sessions and agents screens both render as
    // "Failed to load agents." on the next navigation.
    enabled: waiting,
    refetchInterval: waiting ? DEFENSIVE_REFETCH_MS : false,
    // Pairing is the flow where the user leaves this tab — to type
    // `mobaide-agent pair …` into a terminal. TanStack pauses intervals in an
    // unfocused tab by default, and `queryClient.ts` turns off the refetch on
    // focus, so without this the wait could only resolve through the push.
    refetchIntervalInBackground: true,
  });

  const online =
    waiting && agents.data?.find((agent) => agent.agentId === agentId)?.online === true;

  // Mobile's second `refreshAgents()`, and for the same reason: the claim
  // already refreshed, but a `status_changed` that landed while that request
  // was in flight would be rolled back by its response.
  const refreshedFor = useRef<string | null>(null);
  useEffect(() => {
    if (attemptKey === null || refreshedFor.current === attemptKey) return;
    refreshedFor.current = attemptKey;
    void queryClient.refetchQueries({ queryKey: queryKeys.agents() });
  }, [attemptKey, queryClient]);

  useEffect(() => {
    if (!waiting) {
      setElapsedSeconds(0);
      return;
    }
    setElapsedSeconds(0);
    const handle = setInterval(() => {
      setElapsedSeconds((seconds) => Math.min(seconds + 1, WAIT_FOR_ONLINE_TIMEOUT_SECONDS));
    }, 1000);
    return () => {
      clearInterval(handle);
    };
    // Keyed on the attempt so a second claim, or "Keep waiting", restarts the
    // clock rather than inheriting the first attempt's elapsed count.
  }, [waiting, attemptKey]);

  const reachedDeadline = waiting && elapsedSeconds >= WAIT_FOR_ONLINE_TIMEOUT_SECONDS;

  // The terminal refetch. `refetchQueries` resolves whether or not the GET
  // succeeded, so a relay that cannot be reached still ends the wait. The
  // done-marker deliberately stays out of the dependency list: including it
  // would re-run the cleanup and discard the settle we are waiting for.
  useEffect(() => {
    if (!reachedDeadline || attemptKey === null) return;
    let stale = false;
    void queryClient.refetchQueries({ queryKey: queryKeys.agents() }).finally(() => {
      if (!stale) setFinalCheckDoneFor(attemptKey);
    });
    return () => {
      stale = true;
    };
  }, [reachedDeadline, attemptKey, queryClient]);

  return {
    online,
    elapsedSeconds,
    timedOut: reachedDeadline && !online && finalCheckDoneFor === attemptKey,
  };
}

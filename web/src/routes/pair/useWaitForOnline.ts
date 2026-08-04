/**
 * The post-claim "waiting for the agent" watcher.
 *
 * A successful claim only proves the relay persisted credentials. The agent
 * still has to receive the signed push, verify it, write its `.env` and bring
 * its relay client up — so the authoritative "paired" signal is
 * `agent.status_changed` with `online: true`, never the claim's 200 and never
 * `ws_delivered`.
 *
 * Android runs a coroutine that polls its own cached agent list once a second
 * (`AgentListViewModel.startWaitingForOnlineWatcher`). Web does not need the
 * poll: `agent.status_changed` already invalidates `['agents']` through the
 * event bridge, and an invalidation refetches this mounted query. What web does
 * keep is the reason the mobile watcher refreshes *twice* — a status event
 * delivered between the claim's refresh and the watcher's start would otherwise
 * be overwritten by the in-flight stale snapshot — plus a slow interval so a
 * dropped socket cannot strand the screen for the full minute.
 */
import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { listAgents } from '../../api/agentRepository';
import { queryKeys, wsBackedQueryOptions } from '../../lib/queryKeys';
import { WAIT_FOR_ONLINE_TIMEOUT_SECONDS } from './pairCopy';

/**
 * Slow enough to be a safety net rather than a poll — twelve requests across
 * the whole 60 s window — and only while a wait is actually in progress.
 */
const DEFENSIVE_REFETCH_MS = 5_000;

export interface WaitForOnlineState {
  online: boolean;
  elapsedSeconds: number;
  timedOut: boolean;
}

export function useWaitForOnline(agentId: string | null): WaitForOnlineState {
  const queryClient = useQueryClient();
  const waiting = agentId !== null;
  const [elapsedSeconds, setElapsedSeconds] = useState(0);

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
  });

  const online =
    waiting && agents.data?.find((agent) => agent.agentId === agentId)?.online === true;

  // Mobile's second `refreshAgents()`, and for the same reason: the claim
  // already refreshed, but a `status_changed` that landed while that request
  // was in flight would be rolled back by its response.
  const refreshedFor = useRef<string | null>(null);
  useEffect(() => {
    if (agentId === null || refreshedFor.current === agentId) return;
    refreshedFor.current = agentId;
    void queryClient.refetchQueries({ queryKey: queryKeys.agents() });
  }, [agentId, queryClient]);

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
    // Keyed on the agent id so a second claim restarts the clock rather than
    // inheriting the first attempt's elapsed count.
  }, [waiting, agentId]);

  return {
    online,
    elapsedSeconds,
    timedOut: waiting && !online && elapsedSeconds >= WAIT_FOR_ONLINE_TIMEOUT_SECONDS,
  };
}

/**
 * The Changes viewer's two queries.
 *
 * Polling, not just push: `git.changed` only fires for the app's own git
 * mutations, so a commit typed into the PTY — the whole point of the viewer —
 * never announces itself (Android `GitRepository.observeSessionGitStatus`).
 * One `enabled` gate does all the scheduling: the poll runs only while the
 * panel is open, the tab visible and the window focused, and re-enabling
 * refetches on the spot because the data is kept always-stale — that is
 * "refresh on open", "refresh on focus" (side-by-side windows included, which
 * `visibilitychange` never reports) and "no traffic when hidden" in one place.
 * TanStack's own `refetchOnWindowFocus` is deliberately not used: it only
 * refetches stale data on `visibilitychange`, and it would double-fire here.
 */
import { useSyncExternalStore } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  getSessionGitDiff,
  getSessionGitStatus,
  isTerminalSessionGitError,
  type SessionGitDiffRequest,
  type SessionGitStatus,
} from '../../../api/sessionGitRepository';
import { queryKeys } from '../../../lib/queryKeys';
import { onTabVisibility, tabHidden } from '../../../realtime/tabVisibility';
import { onWindowFocus, windowFocused } from '../../../realtime/windowFocus';

export const SESSION_GIT_POLL_MS = 10_000;

export function useSessionGitStatus(agentId: string, sessionId: string, active: boolean) {
  const focused = useSyncExternalStore(onWindowFocus, windowFocused);
  const hidden = useSyncExternalStore(onTabVisibility, tabHidden);
  const queryKey = queryKeys.sessionGitStatus(agentId, sessionId);
  return useQuery({
    queryKey,
    queryFn: async ({ client, signal }) => {
      // Each request carries the last `etag`, so an idle repo costs a tiny
      // `unchanged` round trip; the cached body is then handed back as is.
      const previous = client.getQueryData<SessionGitStatus>(queryKey);
      const fresh = await getSessionGitStatus(agentId, sessionId, previous?.etag, signal);
      if (!fresh.unchanged) return fresh;
      if (previous !== undefined) return previous;
      return getSessionGitStatus(agentId, sessionId, undefined, signal);
    },
    // The feed ends after an old-agent 404 or a session-not-found 404: neither
    // answer can change, so there is nothing left to poll — and a disabled
    // query is not refetched by the invalidation the failure left behind.
    enabled: (query) =>
      active && focused && !hidden && !isTerminalSessionGitError(query.state.error),
    refetchInterval: SESSION_GIT_POLL_MS,
    staleTime: 0,
    // Failures render inline; the panel sits on the top layer, above the toasts.
    meta: { silent: true },
  });
}

export function useSessionGitDiff(
  agentId: string,
  sessionId: string,
  request: SessionGitDiffRequest,
) {
  return useQuery({
    queryKey: queryKeys.sessionGitDiff(agentId, sessionId, {
      path: request.path,
      scope: request.scope,
      context: request.context,
      ...(request.origPath === undefined ? {} : { origPath: request.origPath }),
    }),
    queryFn: ({ signal }) => getSessionGitDiff(agentId, sessionId, request, signal),
    meta: { silent: true },
  });
}

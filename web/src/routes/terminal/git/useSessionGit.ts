/**
 * The Changes viewer's queries and mutations.
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
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchAgentCapabilities } from '../../../api/agentCapabilities';
import {
  cancelSessionGitJob,
  commitSessionGit,
  discardSessionGit,
  getSessionGitCommitContext,
  getSessionGitDiff,
  getSessionGitJob,
  getSessionGitStatus,
  isSessionGitJobLost,
  isTerminalSessionGitError,
  stageSessionGit,
  startSessionGitJob,
  startSessionGitRebase,
  stepSessionGitRebase,
  unstageSessionGit,
  type SessionGitCommitRequest,
  type SessionGitDiffRequest,
  type SessionGitDiscardScope,
  type SessionGitJob,
  type SessionGitRebaseStep,
  type SessionGitStatus,
  type SessionGitSyncRequest,
} from '../../../api/sessionGitRepository';
import { queryKeys } from '../../../lib/queryKeys';
import { gitCapabilities } from '../../../protocol/dto/agentVersion';
import {
  applySessionGitJob,
  type SessionGitLatestJob,
} from '../../../realtime/sessionGitJobCache';
import { onTabVisibility, tabHidden } from '../../../realtime/tabVisibility';
import { onWindowFocus, windowFocused } from '../../../realtime/windowFocus';

export const SESSION_GIT_POLL_MS = 10_000;
/**
 * The reconcile cadence of a running fetch / pull / push. The socket frames
 * are the fast path; this is what survives a dropped `git.job.done`, a tab
 * that was hidden while the job ran, or a job another client started.
 */
export const SESSION_GIT_JOB_POLL_MS = 3_000;

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
    // A context switch keeps the previous diff, and its Context control, on
    // screen while the new one loads (mobile keeps its last Ready state too).
    // The pane mounts a fresh observer per session and file, so nothing carries
    // across either.
    placeholderData: keepPreviousData,
    meta: { silent: true },
  });
}

/** Re-probed no more often than this: a capability only moves on an agent update. */
export const GIT_CAPABILITIES_STALE_MS = 5 * 60_000;

/**
 * The agent's `capabilities.git`, read once per viewer. Every mutating control
 * hangs off an explicit `true`, so until the probe answers — or when it fails —
 * the viewer is the read-only one older agents get.
 */
export function useSessionGitCapabilities(agentId: string, active: boolean) {
  return useQuery({
    queryKey: queryKeys.agentCapabilities(agentId),
    queryFn: () => fetchAgentCapabilities(agentId),
    enabled: active,
    staleTime: GIT_CAPABILITIES_STALE_MS,
    select: gitCapabilities,
    meta: { silent: true },
  });
}

export function useSessionGitCommitContext(agentId: string, sessionId: string, active: boolean) {
  return useQuery({
    queryKey: queryKeys.sessionGitCommitContext(agentId, sessionId),
    queryFn: ({ signal }) => getSessionGitCommitContext(agentId, sessionId, signal),
    enabled: active,
    staleTime: 0,
    meta: { silent: true },
  });
}

export interface DiscardVariables {
  paths: readonly string[];
  scope: SessionGitDiscardScope;
}

/**
 * The four mutations of one session's staged set. Each settles by re-reading
 * the status only: the panel's etag effect and the agent's `git.changed` echo
 * already re-read every open diff once the tree moved, so invalidating the
 * whole prefix here would only start a third `git diff` for the old scope.
 * A commit also drops the commit context (the previous commit changed).
 * Failures render inline in the panel (top layer, above the toasts), so no
 * global handler is involved.
 */
export function useSessionGitMutations(agentId: string, sessionId: string) {
  const queryClient = useQueryClient();
  const settle = async (): Promise<void> => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.sessionGitStatus(agentId, sessionId),
    });
  };
  const settleCommit = async (): Promise<void> => {
    await Promise.all([
      settle(),
      queryClient.invalidateQueries({
        queryKey: queryKeys.sessionGitCommitContext(agentId, sessionId),
      }),
    ]);
  };
  const stage = useMutation({
    mutationFn: (paths: readonly string[]) => stageSessionGit(agentId, sessionId, paths),
    onSettled: settle,
  });
  const unstage = useMutation({
    mutationFn: (paths: readonly string[]) => unstageSessionGit(agentId, sessionId, paths),
    onSettled: settle,
  });
  const discard = useMutation({
    mutationFn: ({ paths, scope }: DiscardVariables) =>
      discardSessionGit(agentId, sessionId, paths, scope),
    onSettled: settle,
  });
  const commit = useMutation({
    mutationFn: (request: SessionGitCommitRequest) => commitSessionGit(agentId, sessionId, request),
    onSettled: settleCommit,
  });
  const busy = stage.isPending || unstage.isPending || discard.isPending || commit.isPending;
  return { stage, unstage, discard, commit, busy };
}

/**
 * Start a rebase, and continue / skip / abort the one in progress. Each answer
 * is final — the agent runs git to completion within the request — so the
 * status re-read is all that follows; the banner comes from that status.
 */
export function useSessionGitRebase(agentId: string, sessionId: string) {
  const queryClient = useQueryClient();
  const settle = async (): Promise<void> => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.sessionGitStatus(agentId, sessionId),
    });
  };
  const start = useMutation({
    mutationFn: (onto: string) => startSessionGitRebase(agentId, sessionId, onto),
    onSettled: settle,
  });
  const step = useMutation({
    mutationFn: (which: SessionGitRebaseStep) => stepSessionGitRebase(agentId, sessionId, which),
    onSettled: settle,
  });
  return { start, step, busy: start.isPending || step.isPending };
}

/**
 * The newest fetch / pull / push job the socket reported for one session.
 * Never fetched: the frames write it (`sessionGitJobCache.ts`), so a panel
 * that opens mid-job, or a second tab, adopts the job from here.
 */
export function useSessionGitLatestJob(agentId: string, sessionId: string) {
  return useQuery<SessionGitLatestJob>({
    queryKey: queryKeys.sessionGitLatestJob(agentId, sessionId),
    queryFn: () => Promise.reject(new Error('written by the socket, never fetched')),
    enabled: false,
    staleTime: Infinity,
    meta: { silent: true },
  });
}

/**
 * One job's snapshot, polled every 3 s while it runs and the panel is in a
 * visible, focused tab — the same gate as the status poll — and left alone
 * once it is `done` (or the agent has forgotten it), so the `git.changed`
 * invalidation that follows every job never refetches a finished one into a
 * 404 after the agent's retention window.
 */
export function useSessionGitJob(agentId: string, sessionId: string, jobId: string | null) {
  const focused = useSyncExternalStore(onWindowFocus, windowFocused);
  const hidden = useSyncExternalStore(onTabVisibility, tabHidden);
  const queryKey = queryKeys.sessionGitJob(agentId, sessionId, jobId ?? '');
  return useQuery({
    queryKey,
    // Monotonic like the socket path: a poll the agent answered just before
    // the job finished can land after the `done` frame, and disabling the
    // query does not abort a fetch already in flight.
    queryFn: async ({ client, signal }) => {
      const fresh = await getSessionGitJob(agentId, sessionId, jobId ?? '', signal);
      const cached = client.getQueryData<SessionGitJob>(queryKey);
      return cached?.status === 'done' && fresh.status !== 'done' ? cached : fresh;
    },
    enabled: (query) =>
      jobId !== null &&
      focused &&
      !hidden &&
      query.state.data?.status !== 'done' &&
      !isSessionGitJobLost(query.state.error) &&
      !isTerminalSessionGitError(query.state.error),
    refetchInterval: SESSION_GIT_JOB_POLL_MS,
    staleTime: 0,
    meta: { silent: true },
  });
}

/**
 * Start and cancel. Each answer is a job snapshot and is written into the
 * cache at once, so the panel's banner appears without waiting for the first
 * socket frame; the job's outcome arrives through the frames and the poll.
 */
export function useSessionGitSync(agentId: string, sessionId: string) {
  const queryClient = useQueryClient();
  const adopt = (job: SessionGitJob): void => {
    applySessionGitJob(queryClient, agentId, job);
  };
  const start = useMutation({
    mutationFn: (request: SessionGitSyncRequest) => startSessionGitJob(agentId, sessionId, request),
    onSuccess: adopt,
  });
  const cancel = useMutation({
    mutationFn: (jobId: string) => cancelSessionGitJob(agentId, sessionId, jobId),
    onSuccess: adopt,
  });
  return { start, cancel };
}

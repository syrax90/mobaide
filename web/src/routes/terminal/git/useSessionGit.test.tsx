import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, render, waitFor, type RenderResult } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  SessionGitCommitResult,
  SessionGitJob,
  SessionGitStatus,
} from '../../../api/sessionGitRepository';
import type { SessionGitLatestJob } from '../../../realtime/sessionGitJobCache';
import { fakeEventTarget } from '../../../realtime/__testing__/fakeEventTarget';
import type { FocusWin } from '../../../realtime/windowFocus';

// The collaborator is the repository, not the wire. Both network functions are
// shadowed: `getSessionGitStatus` is the poll under test, and the diff must not
// reach the real client through the `...actual` spread. What each poll carried
// as its etag is recorded, because the round trip is the point of the etag.
const api = vi.hoisted(() => ({
  etags: [] as (string | undefined)[],
  answers: [] as (() => Promise<SessionGitStatus>)[],
  /** What the next stage request answers; the mutation is only a trigger here. */
  stage: undefined as (() => Promise<void>) | undefined,
  commit: undefined as (() => Promise<SessionGitCommitResult>) | undefined,
  /** What each reconcile poll of a job answers, and the ids it was asked for. */
  jobs: [] as (() => Promise<SessionGitJob>)[],
  jobPolls: [] as string[],
  startJob: undefined as (() => Promise<SessionGitJob>) | undefined,
  cancelJob: undefined as (() => Promise<SessionGitJob>) | undefined,
}));

vi.mock('../../../api/sessionGitRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/sessionGitRepository')>();
  return {
    ...actual,
    getSessionGitStatus: (_agentId: string, _sessionId: string, etag?: string) => {
      api.etags.push(etag);
      const next = api.answers.shift();
      return next === undefined ? Promise.reject(new Error('unexpected status fetch')) : next();
    },
    getSessionGitDiff: () => Promise.reject(new Error('diff is not under test')),
    stageSessionGit: () =>
      api.stage === undefined ? Promise.reject(new Error('stage is not under test')) : api.stage(),
    commitSessionGit: () =>
      api.commit === undefined
        ? Promise.reject(new Error('commit is not under test'))
        : api.commit(),
    getSessionGitJob: (_agentId: string, _sessionId: string, jobId: string) => {
      api.jobPolls.push(jobId);
      const next = api.jobs.shift();
      return next === undefined ? Promise.reject(new Error('unexpected job poll')) : next();
    },
    startSessionGitJob: () =>
      api.startJob === undefined
        ? Promise.reject(new Error('start is not under test'))
        : api.startJob(),
    cancelSessionGitJob: () =>
      api.cancelJob === undefined
        ? Promise.reject(new Error('cancel is not under test'))
        : api.cancelJob(),
  };
});

const { toApiError } = await import('../../../api/errors');
const { isSessionGitJobLost, toSessionGitError } = await import(
  '../../../api/sessionGitRepository'
);
const { applySessionGitJob } = await import('../../../realtime/sessionGitJobCache');
const { queryKeys } = await import('../../../lib/queryKeys');
const { observeTabVisibility } = await import('../../../realtime/tabVisibility');
const { observeWindowFocus } = await import('../../../realtime/windowFocus');
const {
  SESSION_GIT_JOB_POLL_MS,
  SESSION_GIT_POLL_MS,
  useSessionGitJob,
  useSessionGitLatestJob,
  useSessionGitMutations,
  useSessionGitStatus,
  useSessionGitSync,
} = await import('./useSessionGit');

const body = (etag: string): SessionGitStatus => ({
  session_id: 's1',
  cwd: '/srv/p1',
  etag,
  unchanged: false,
  unavailable_reason: null,
  repo: null,
  entries: [],
  counts: null,
  truncated: false,
  entry_limit: 5000,
});

/** The tiny `unchanged` round trip an idle repo answers with. */
const unchanged = (etag: string): SessionGitStatus => ({
  session_id: 's1',
  etag,
  unchanged: true,
  truncated: false,
  entry_limit: 5000,
});

const answer = (status: SessionGitStatus): void => {
  api.answers.push(() => Promise.resolve(status));
};

const fail = (error: Error): void => {
  api.answers.push(() => Promise.reject(error));
};

/** A wire failure as `getSessionGitStatus` would throw it. */
const wireFailure = (status: number, detail: unknown): Error => {
  const error = toSessionGitError(toApiError(new Response(null, { status }), { detail }));
  if (!(error instanceof Error)) throw new Error('expected an Error off the wire');
  return error;
};

/** Same wrap as `windowFocus.test.ts`: `FocusWin` speaks `hasFocus()`. */
function fakeWin() {
  const target = fakeEventTarget();
  const state = { focused: true };
  const win: FocusWin = {
    hasFocus: () => state.focused,
    addEventListener: target.addEventListener,
    removeEventListener: target.removeEventListener,
  };
  return {
    win,
    blur: () => {
      state.focused = false;
      target.fire('blur');
    },
    focus: () => {
      state.focused = true;
      target.fire('focus');
    },
  };
}

/**
 * What a render saw, read during render: `useQuery` re-renders only for the
 * properties a render touched.
 */
interface Snapshot {
  data: SessionGitStatus | undefined;
  error: unknown;
  fetchStatus: 'fetching' | 'paused' | 'idle';
}

interface JobSnapshot {
  data: SessionGitJob | undefined;
  error: unknown;
  fetchStatus: 'fetching' | 'paused' | 'idle';
}

let client: QueryClient;
let latest: Snapshot | undefined;
let latestMutations: ReturnType<typeof useSessionGitMutations> | undefined;
let latestJob: JobSnapshot | undefined;
let latestPointer: SessionGitLatestJob | undefined;
let latestSync: ReturnType<typeof useSessionGitSync> | undefined;
const uninstallers: (() => void)[] = [];

function Harness({ active }: { active: boolean }) {
  const { data, error, fetchStatus } = useSessionGitStatus('a1', 's1', active);
  latest = { data, error, fetchStatus };
  return null;
}

/** The poll beside the mutations, as the panel mounts them. */
function MutationsHarness() {
  const { data, error, fetchStatus } = useSessionGitStatus('a1', 's1', true);
  latest = { data, error, fetchStatus };
  latestMutations = useSessionGitMutations('a1', 's1');
  return null;
}

const mutations = (): ReturnType<typeof useSessionGitMutations> => {
  if (latestMutations === undefined) throw new Error('mutations not rendered');
  return latestMutations;
};

const subject = (active: boolean) => (
  <QueryClientProvider client={client}>
    <Harness active={active} />
  </QueryClientProvider>
);

function renderHook(active: boolean): RenderResult {
  return render(subject(active));
}

/** The last render's query result; every case renders before reading it. */
const hook = (): Snapshot => {
  if (latest === undefined) throw new Error('hook not rendered');
  return latest;
};

/** React Query notifies on a timer, so nothing it does lands in this task. */
const flush = async (): Promise<void> => {
  await act(async () => {
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
  });
};

const invalidate = async (): Promise<void> => {
  await act(async () => {
    await client.invalidateQueries({ queryKey: queryKeys.sessionGitStatus('a1', 's1') });
  });
};

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  latest = undefined;
  latestMutations = undefined;
  api.etags.length = 0;
  api.answers.length = 0;
  api.stage = undefined;
  api.commit = undefined;
  api.jobs.length = 0;
  api.jobPolls.length = 0;
  api.startJob = undefined;
  api.cancelJob = undefined;
  latestJob = undefined;
  latestPointer = undefined;
  latestSync = undefined;
});

afterEach(() => {
  cleanup();
  client.clear();
  for (const uninstall of uninstallers.splice(0)) uninstall();
  vi.useRealTimers();
});

describe('useSessionGitStatus gating', () => {
  it('fetches nothing while the viewer is not active', async () => {
    renderHook(false);
    await flush();

    expect(api.etags).toEqual([]);
    expect(hook().fetchStatus).toBe('idle');
    expect(hook().data).toBeUndefined();
  });

  it('fetches on activation and again, at once, on every re-activation', async () => {
    // Kept always-stale, so re-enabling is "refresh on open": the panel
    // remounts the hook each time it opens and must not show last time's tree.
    answer(body('e1'));
    answer(body('e2'));
    const view = renderHook(true);
    await waitFor(() => {
      expect(hook().data?.etag).toBe('e1');
    });

    view.rerender(subject(false));
    await flush();
    expect(api.etags).toHaveLength(1);

    view.rerender(subject(true));
    await waitFor(() => {
      expect(hook().data?.etag).toBe('e2');
    });
    expect(api.etags).toHaveLength(2);
  });

  it('pauses while the window is blurred and refetches when focus returns', async () => {
    const w = fakeWin();
    uninstallers.push(observeWindowFocus(w.win));
    answer(body('e1'));
    answer(body('e2'));
    renderHook(true);
    await waitFor(() => {
      expect(hook().data?.etag).toBe('e1');
    });

    act(() => {
      w.blur();
    });
    await invalidate();
    await flush();

    // A disabled query is not refetched by an invalidation, and the poll
    // timer is not armed for it either.
    expect(api.etags).toHaveLength(1);
    expect(hook().fetchStatus).toBe('idle');

    act(() => {
      w.focus();
    });

    await waitFor(() => {
      expect(hook().data?.etag).toBe('e2');
    });
    expect(api.etags).toEqual([undefined, 'e1']);
  });

  it('pauses while the tab is hidden and refetches when it is shown again', async () => {
    const doc = fakeEventTarget();
    uninstallers.push(observeTabVisibility(doc));
    answer(body('e1'));
    answer(body('e2'));
    renderHook(true);
    await waitFor(() => {
      expect(hook().data?.etag).toBe('e1');
    });

    act(() => {
      doc.hidden = true;
      doc.fire('visibilitychange');
    });
    await invalidate();
    await flush();

    expect(api.etags).toHaveLength(1);

    act(() => {
      doc.hidden = false;
      doc.fire('visibilitychange');
    });

    await waitFor(() => {
      expect(hook().data?.etag).toBe('e2');
    });
  });

  it('polls on the interval while active, focused and visible, carrying the etag', async () => {
    vi.useFakeTimers();
    answer(body('e1'));
    answer(body('e2'));
    renderHook(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(api.etags).toEqual([undefined]);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SESSION_GIT_POLL_MS + 5);
    });

    expect(api.etags).toEqual([undefined, 'e1']);
    expect(hook().data?.etag).toBe('e2');
  });
});

describe('useSessionGitStatus etag round trip', () => {
  it('sends the cached etag and keeps the cached body when the agent answers unchanged', async () => {
    answer(body('e1'));
    answer(unchanged('e1'));
    const view = renderHook(true);
    await waitFor(() => {
      expect(hook().data?.etag).toBe('e1');
    });
    const first = hook().data;

    view.rerender(subject(false));
    view.rerender(subject(true));
    await waitFor(() => {
      expect(api.etags).toHaveLength(2);
    });
    await flush();

    expect(api.etags).toEqual([undefined, 'e1']);
    // The same object, not a copy and not the bodiless `unchanged` answer.
    expect(hook().data).toBe(first);
    expect(hook().data?.unchanged).toBe(false);
  });

  it('asks again without an etag when unchanged arrives over an empty cache', async () => {
    // Nothing was sent to be unchanged against, so an agent that still says so
    // is asked for the body outright rather than leaving the viewer empty.
    answer(unchanged('e1'));
    answer(body('e1'));
    renderHook(true);

    await waitFor(() => {
      expect(hook().data?.etag).toBe('e1');
    });

    expect(api.etags).toEqual([undefined, undefined]);
    expect(hook().data?.unchanged).toBe(false);
  });
});

describe('useSessionGitStatus failures', () => {
  it('stops after a session-gone 404: no poll, no refetch on invalidation or re-activation', async () => {
    fail(wireFailure(404, "Session 's1' not found"));
    const view = renderHook(true);
    await waitFor(() => {
      expect(hook().error).not.toBeNull();
    });

    await invalidate();
    view.rerender(subject(false));
    view.rerender(subject(true));
    await flush();

    expect(api.etags).toHaveLength(1);
    expect(hook().fetchStatus).toBe('idle');
    expect(hook().error).toMatchObject({ kind: 'sessionGone' });
  });

  it('stops after an old-agent 404 as well', async () => {
    fail(wireFailure(404, 'Unknown proxy route'));
    renderHook(true);
    await waitFor(() => {
      expect(hook().error).toMatchObject({ kind: 'agentOutdated' });
    });

    await invalidate();
    await flush();

    expect(api.etags).toHaveLength(1);
  });

  it('keeps going after a git failure, which a retry can change', async () => {
    fail(wireFailure(504, { code: 'git_timeout' }));
    answer(body('e1'));
    renderHook(true);
    await waitFor(() => {
      expect(hook().error).toMatchObject({ kind: 'git', code: 'git_timeout' });
    });

    await invalidate();

    await waitFor(() => {
      expect(hook().data?.etag).toBe('e1');
    });
    expect(hook().error).toBeNull();
    expect(api.etags).toEqual([undefined, undefined]);
  });

  it('keeps the last body on screen when a later poll fails', async () => {
    // A failed poll over a cached body reports the failure beside the list
    // rather than blanking it, as both mobile clients do.
    answer(body('e1'));
    fail(new TypeError('Failed to fetch'));
    renderHook(true);
    await waitFor(() => {
      expect(hook().data?.etag).toBe('e1');
    });

    await invalidate();

    await waitFor(() => {
      expect(hook().error).toBeInstanceOf(TypeError);
    });
    expect(hook().data?.etag).toBe('e1');
  });

  it('opts the poll out of the global error toast', async () => {
    fail(new TypeError('Failed to fetch'));
    renderHook(true);
    await waitFor(() => {
      expect(hook().error).not.toBeNull();
    });

    expect(client.getQueryCache().find({ queryKey: queryKeys.sessionGitStatus('a1', 's1') })?.meta)
      .toEqual({ silent: true });
  });
});

describe('useSessionGitMutations', () => {
  const renderMutations = async (): Promise<void> => {
    answer(body('e1'));
    answer(body('e2'));
    render(
      <QueryClientProvider client={client}>
        <MutationsHarness />
      </QueryClientProvider>,
    );
    await waitFor(() => {
      expect(hook().data?.etag).toBe('e1');
    });
  };

  it('re-reads the status, carrying its etag, once a mutation has settled', async () => {
    api.stage = () => Promise.resolve();
    await renderMutations();

    await act(async () => {
      await mutations().stage.mutateAsync(['src/app.py']);
    });

    await waitFor(() => {
      expect(hook().data?.etag).toBe('e2');
    });
    expect(api.etags).toEqual([undefined, 'e1']);
  });

  it('re-reads after a refusal as well, since the tree may have moved underneath', async () => {
    api.stage = () => Promise.reject(wireFailure(409, { code: 'pathspec_mismatch', message: 'x' }));
    await renderMutations();

    await act(async () => {
      await expect(mutations().stage.mutateAsync(['gone.py'])).rejects.toMatchObject({
        kind: 'git',
        code: 'pathspec_mismatch',
      });
    });

    await waitFor(() => {
      expect(hook().data?.etag).toBe('e2');
    });
  });

  /** A diff pane and a commit sheet, cached as the panel would have them. */
  const diffKey = () =>
    queryKeys.sessionGitDiff('a1', 's1', { path: 'src/app.py', scope: 'unstaged', context: 3 });
  const contextKey = () => queryKeys.sessionGitCommitContext('a1', 's1');
  const seedSiblings = (): void => {
    client.setQueryData(diffKey(), { path: 'src/app.py', scope: 'unstaged' });
    client.setQueryData(contextKey(), { author: null, head: null });
  };
  const invalidated = (queryKey: readonly unknown[]): boolean | undefined =>
    client.getQueryState(queryKey)?.isInvalidated;

  it('re-reads only the status after a stage: open diffs and the commit context stay fresh', async () => {
    // The panel's etag effect and the agent's `git.changed` echo already
    // re-read every open diff once the tree moved; a third `git diff` for the
    // old scope would only race them.
    api.stage = () => Promise.resolve();
    await renderMutations();
    seedSiblings();

    await act(async () => {
      await mutations().stage.mutateAsync(['src/app.py']);
    });

    await waitFor(() => {
      expect(hook().data?.etag).toBe('e2');
    });
    expect(invalidated(diffKey())).toBe(false);
    expect(invalidated(contextKey())).toBe(false);
  });

  it('drops the commit context, but still not the diffs, once a commit has settled', async () => {
    // The previous commit changed, so the sheet's Amend prefill is stale.
    api.commit = () =>
      Promise.resolve({ oid: 'abc1234def', short_oid: 'abc1234', subject: 'Smoke' });
    await renderMutations();
    seedSiblings();

    await act(async () => {
      await mutations().commit.mutateAsync({ message: 'Smoke', amend: false, no_verify: false });
    });

    await waitFor(() => {
      expect(hook().data?.etag).toBe('e2');
    });
    expect(invalidated(contextKey())).toBe(true);
    expect(invalidated(diffKey())).toBe(false);
  });

  it('reports one busy flag while any of the mutations is in flight', async () => {
    let release: (() => void) | undefined;
    api.stage = () =>
      new Promise<void>((resolve) => {
        release = resolve;
      });
    await renderMutations();
    expect(mutations().busy).toBe(false);

    act(() => {
      void mutations().stage.mutateAsync(['src/app.py']);
    });
    await waitFor(() => {
      expect(mutations().busy).toBe(true);
    });

    await act(async () => {
      release?.();
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(mutations().busy).toBe(false);
    });
  });
});

/** The job poll beside the socket pointer and the start / cancel pair, as the panel mounts them. */
function JobHarness({ jobId }: { jobId: string | null }) {
  const { data, error, fetchStatus } = useSessionGitJob('a1', 's1', jobId);
  latestJob = { data, error, fetchStatus };
  latestPointer = useSessionGitLatestJob('a1', 's1').data;
  latestSync = useSessionGitSync('a1', 's1');
  return null;
}

const jobSubject = (jobId: string | null) => (
  <QueryClientProvider client={client}>
    <JobHarness jobId={jobId} />
  </QueryClientProvider>
);

const jobHook = (): JobSnapshot => {
  if (latestJob === undefined) throw new Error('job hook not rendered');
  return latestJob;
};

const sync = (): ReturnType<typeof useSessionGitSync> => {
  if (latestSync === undefined) throw new Error('sync not rendered');
  return latestSync;
};

const snapshot = (over: Partial<SessionGitJob> = {}): SessionGitJob => ({
  job_id: 'j1',
  session_id: 's1',
  op: 'push',
  status: 'running',
  phase: null,
  percent: null,
  line: null,
  ok: null,
  error_code: null,
  message: null,
  ...over,
});

const answerJob = (job: SessionGitJob): void => {
  api.jobs.push(() => Promise.resolve(job));
};

const failJob = (error: Error): void => {
  api.jobs.push(() => Promise.reject(error));
};

/** The `git.changed` that follows every job invalidates the whole session prefix. */
const invalidateSession = async (): Promise<void> => {
  await act(async () => {
    await client.invalidateQueries({ queryKey: queryKeys.sessionGit('a1', 's1') });
  });
};

describe('useSessionGitJob', () => {
  it('asks nothing without a job id', async () => {
    render(jobSubject(null));
    await flush();

    expect(api.jobPolls).toEqual([]);
    expect(jobHook().fetchStatus).toBe('idle');
    expect(jobHook().data).toBeUndefined();
  });

  it('polls every 3 s while the job runs and stops once it is done', async () => {
    vi.useFakeTimers();
    answerJob(snapshot({ percent: 10 }));
    answerJob(snapshot({ percent: 60 }));
    answerJob(snapshot({ status: 'done', ok: true, message: 'main -> main' }));
    render(jobSubject('j1'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(api.jobPolls).toEqual(['j1']);
    expect(jobHook().data?.percent).toBe(10);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SESSION_GIT_JOB_POLL_MS + 5);
    });
    expect(api.jobPolls).toHaveLength(2);
    expect(jobHook().data?.percent).toBe(60);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SESSION_GIT_JOB_POLL_MS + 5);
    });
    expect(jobHook().data?.status).toBe('done');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SESSION_GIT_JOB_POLL_MS * 3);
    });
    expect(api.jobPolls).toHaveLength(3);
    expect(jobHook().fetchStatus).toBe('idle');
  });

  it('stops polling as soon as a socket frame says done, even mid-interval', async () => {
    vi.useFakeTimers();
    answerJob(snapshot({ percent: 10 }));
    render(jobSubject('j1'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });

    act(() => {
      applySessionGitJob(client, 'a1', snapshot({ status: 'done', ok: true }));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SESSION_GIT_JOB_POLL_MS * 3);
    });

    expect(api.jobPolls).toEqual(['j1']);
    expect(jobHook().data?.status).toBe('done');
  });

  it('keeps the done frame when a poll it overtook answers running afterwards', async () => {
    // Disabling the query does not abort a fetch already in flight: a poll the
    // agent answered just before the job finished lands after `git.job.done`
    // and must not put the banner back to "Pushing…".
    vi.useFakeTimers();
    let release: ((job: SessionGitJob) => void) | undefined;
    api.jobs.push(
      () =>
        new Promise<SessionGitJob>((resolve) => {
          release = resolve;
        }),
    );
    render(jobSubject('j1'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(api.jobPolls).toEqual(['j1']);
    expect(jobHook().data).toBeUndefined();

    act(() => {
      applySessionGitJob(
        client,
        'a1',
        snapshot({ status: 'done', ok: true, message: 'main -> main' }),
      );
    });
    await act(async () => {
      release?.(snapshot({ phase: 'Writing objects', percent: 99 }));
      await vi.advanceTimersByTimeAsync(1);
    });

    expect(jobHook().data).toMatchObject({ status: 'done', ok: true, message: 'main -> main' });
    expect(jobHook().data?.percent).toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SESSION_GIT_JOB_POLL_MS * 3);
    });
    expect(api.jobPolls).toEqual(['j1']);
    expect(jobHook().fetchStatus).toBe('idle');
  });

  it('does not refetch a done job on the git.changed invalidation that follows it', async () => {
    // The agent forgets a finished job after ten minutes; a refetch then would
    // answer `job_not_found` and read as a lost job for a result already shown.
    answerJob(snapshot({ status: 'done', ok: true }));
    render(jobSubject('j1'));
    await waitFor(() => {
      expect(jobHook().data?.status).toBe('done');
    });

    await invalidateSession();
    await flush();

    expect(api.jobPolls).toEqual(['j1']);
    expect(jobHook().fetchStatus).toBe('idle');
  });

  it('stops on job_not_found and reports the job as lost', async () => {
    failJob(wireFailure(404, { code: 'job_not_found' }));
    render(jobSubject('j1'));
    await waitFor(() => {
      expect(jobHook().error).toMatchObject({ kind: 'git', code: 'job_not_found' });
    });

    await invalidateSession();
    await flush();

    expect(api.jobPolls).toEqual(['j1']);
    expect(jobHook().fetchStatus).toBe('idle');
    expect(isSessionGitJobLost(jobHook().error)).toBe(true);
  });

  it('keeps polling after a transient failure', async () => {
    vi.useFakeTimers();
    failJob(new TypeError('Failed to fetch'));
    answerJob(snapshot({ percent: 10 }));
    render(jobSubject('j1'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(jobHook().error).toBeInstanceOf(TypeError);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SESSION_GIT_JOB_POLL_MS + 5);
    });

    expect(api.jobPolls).toEqual(['j1', 'j1']);
    expect(jobHook().data?.percent).toBe(10);
    expect(jobHook().error).toBeNull();
  });

  it('pauses while the tab is hidden and re-reads at once when it is shown again', async () => {
    const doc = fakeEventTarget();
    uninstallers.push(observeTabVisibility(doc));
    answerJob(snapshot({ percent: 10 }));
    answerJob(snapshot({ percent: 20 }));
    render(jobSubject('j1'));
    await waitFor(() => {
      expect(jobHook().data?.percent).toBe(10);
    });

    act(() => {
      doc.hidden = true;
      doc.fire('visibilitychange');
    });
    await invalidateSession();
    await flush();
    expect(api.jobPolls).toHaveLength(1);

    act(() => {
      doc.hidden = false;
      doc.fire('visibilitychange');
    });

    await waitFor(() => {
      expect(jobHook().data?.percent).toBe(20);
    });
  });

  it('opts the poll out of the global error toast', async () => {
    failJob(new TypeError('Failed to fetch'));
    render(jobSubject('j1'));
    await waitFor(() => {
      expect(jobHook().error).not.toBeNull();
    });

    expect(
      client.getQueryCache().find({ queryKey: queryKeys.sessionGitJob('a1', 's1', 'j1') })?.meta,
    ).toEqual({ silent: true });
  });
});

describe('useSessionGitLatestJob', () => {
  it('never fetches and reads what the socket wrote', async () => {
    render(jobSubject(null));
    await flush();
    expect(latestPointer).toBeUndefined();

    act(() => {
      applySessionGitJob(client, 'a1', snapshot({ job_id: 'j7', op: 'fetch' }));
    });

    await waitFor(() => {
      expect(latestPointer).toEqual({ jobId: 'j7' });
    });
    expect(api.jobPolls).toEqual([]);
  });
});

describe('useSessionGitSync', () => {
  it('writes the started job and the session pointer into the cache', async () => {
    api.startJob = () => Promise.resolve(snapshot({ job_id: 'j3', op: 'fetch' }));
    render(jobSubject(null));
    await flush();

    await act(async () => {
      await sync().start.mutateAsync({ op: 'fetch' });
    });

    expect(client.getQueryData(queryKeys.sessionGitJob('a1', 's1', 'j3'))).toMatchObject({
      job_id: 'j3',
      op: 'fetch',
      status: 'running',
    });
    await waitFor(() => {
      expect(latestPointer).toEqual({ jobId: 'j3' });
    });
    expect(api.jobPolls).toEqual([]);
  });

  it('writes the cancel answer over the running snapshot without another poll', async () => {
    answerJob(snapshot({ percent: 10 }));
    api.cancelJob = () =>
      Promise.resolve(
        snapshot({ status: 'done', ok: false, error_code: 'cancelled', message: 'cancelled' }),
      );
    render(jobSubject('j1'));
    await waitFor(() => {
      expect(jobHook().data?.percent).toBe(10);
    });

    await act(async () => {
      await sync().cancel.mutateAsync('j1');
    });

    await waitFor(() => {
      expect(jobHook().data).toMatchObject({ status: 'done', ok: false, error_code: 'cancelled' });
    });
    expect(jobHook().fetchStatus).toBe('idle');
    expect(api.jobPolls).toEqual(['j1']);
  });
});

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, render, waitFor, type RenderResult } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  SessionGitCommitResult,
  SessionGitStatus,
} from '../../../api/sessionGitRepository';
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
  };
});

const { toApiError } = await import('../../../api/errors');
const { toSessionGitError } = await import('../../../api/sessionGitRepository');
const { queryKeys } = await import('../../../lib/queryKeys');
const { observeTabVisibility } = await import('../../../realtime/tabVisibility');
const { observeWindowFocus } = await import('../../../realtime/windowFocus');
const { SESSION_GIT_POLL_MS, useSessionGitMutations, useSessionGitStatus } = await import(
  './useSessionGit'
);

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

let client: QueryClient;
let latest: Snapshot | undefined;
let latestMutations: ReturnType<typeof useSessionGitMutations> | undefined;
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

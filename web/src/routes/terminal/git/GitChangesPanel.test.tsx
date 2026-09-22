import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  SessionGitCommitContext,
  SessionGitCommitRequest,
  SessionGitCommitResult,
  SessionGitConflict,
  SessionGitConflictSide,
  SessionGitDiff,
  SessionGitDiffRequest,
  SessionGitDiscardScope,
  SessionGitEntry,
  SessionGitJob,
  SessionGitRebaseInfo,
  SessionGitRebaseResult,
  SessionGitRebaseStep,
  SessionGitResolveRequest,
  SessionGitRestoreRequest,
  SessionGitStatus,
  SessionGitSyncRequest,
} from '../../../api/sessionGitRepository';
import type { AgentVersionInfo } from '../../../protocol/dto/agentVersion';

/** One mutating request as the panel issued it. */
type MutationCall =
  | { kind: 'stage' | 'unstage'; paths: string[] }
  | { kind: 'discard'; paths: string[]; scope: SessionGitDiscardScope }
  | { kind: 'commit'; request: SessionGitCommitRequest };

/** One write of the conflict resolver as the panel issued it. */
type ConflictCall =
  | { kind: 'resolve'; request: SessionGitResolveRequest }
  | { kind: 'take'; path: string; side: SessionGitConflictSide }
  | { kind: 'restore'; request: SessionGitRestoreRequest };

// The network functions shadowed, as in `useSessionGit.test.tsx`: the status
// the next poll answers with is whatever `api.status` holds, and every diff
// request is recorded, because the scope it asks for is what a selection that
// followed its file across sections must get right. A diff answers with the
// request echoed plus `api.shape` (which also sees the session asked), and
// while `api.hold` is set it waits in `api.release` so a case can look at the
// pane mid-load. Every mutation is recorded in `api.mutations` and succeeds
// unless `api.refuse` names a failure for its kind; the commit sheet reads
// `api.commitContext` on open. `api.version` is the agent's version probe,
// whose `capabilities.git` gates every mutating control.
const api = vi.hoisted(() => ({
  status: undefined as SessionGitStatus | undefined,
  diffs: [] as SessionGitDiffRequest[],
  shape: undefined as
    | ((request: SessionGitDiffRequest, sessionId: string) => Partial<SessionGitDiff>)
    | undefined,
  hold: false,
  release: [] as (() => void)[],
  version: undefined as AgentVersionInfo | undefined,
  mutations: [] as MutationCall[],
  refuse: undefined as ((kind: MutationCall['kind']) => unknown) | undefined,
  commitContext: undefined as SessionGitCommitContext | undefined,
  commitResult: undefined as SessionGitCommitResult | undefined,
  /**
   * The fetch / pull / push jobs: every start is recorded and answers
   * `startResult` (or rejects with `refuseStart`); the reconcile poll answers
   * `job` (or rejects with `jobError`) and records the ids it was asked for;
   * cancel records the id and answers the job as cancelled.
   */
  starts: [] as SessionGitSyncRequest[],
  startResult: undefined as SessionGitJob | undefined,
  refuseStart: undefined as Error | undefined,
  /** Hold every start until `releaseStart` is drained, as `hold` does for diffs. */
  holdStart: false,
  releaseStart: [] as (() => void)[],
  job: undefined as SessionGitJob | undefined,
  jobError: undefined as Error | undefined,
  jobPolls: [] as string[],
  cancels: [] as string[],
  /**
   * The rebase routes: every start and step is recorded and answers
   * `rebaseResult`, unless `refuseRebase` names a failure for the call.
   */
  rebases: [] as ({ kind: 'start'; onto: string } | { kind: SessionGitRebaseStep })[],
  rebaseResult: undefined as SessionGitRebaseResult | undefined,
  refuseRebase: undefined as Error | undefined,
  /**
   * The conflict resolver: a read answers `conflictFile` and records the path
   * it asked for; every write is recorded and succeeds unless
   * `refuseConflict` holds a failure.
   */
  conflictFile: undefined as SessionGitConflict | undefined,
  conflictReads: [] as string[],
  conflictCalls: [] as ConflictCall[],
  refuseConflict: undefined as Error | undefined,
}));

vi.mock('../../../api/sessionGitRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/sessionGitRepository')>();
  const rebaseCall = (call: (typeof api.rebases)[number]): Promise<SessionGitRebaseResult> => {
    api.rebases.push(call);
    if (api.refuseRebase !== undefined) return Promise.reject(api.refuseRebase);
    return api.rebaseResult === undefined
      ? Promise.reject(new Error('no rebase result staged'))
      : Promise.resolve(api.rebaseResult);
  };
  const conflictCall = (call: ConflictCall): Promise<void> => {
    api.conflictCalls.push(call);
    return api.refuseConflict === undefined ? Promise.resolve() : Promise.reject(api.refuseConflict);
  };
  const mutate = <T,>(call: MutationCall, result: () => T): Promise<T> => {
    api.mutations.push(call);
    const refusal = api.refuse?.(call.kind);
    if (refusal === undefined) return Promise.resolve(result());
    return Promise.reject(refusal instanceof Error ? refusal : new Error('refused'));
  };
  return {
    ...actual,
    getSessionGitStatus: () =>
      api.status === undefined
        ? Promise.reject(new Error('no status body staged'))
        : Promise.resolve(api.status),
    getSessionGitDiff: (_agentId: string, sessionId: string, request: SessionGitDiffRequest) => {
      api.diffs.push(request);
      const body = { ...diff(request), ...api.shape?.(request, sessionId) };
      if (!api.hold) return Promise.resolve(body);
      return new Promise<SessionGitDiff>((resolve) => {
        api.release.push(() => {
          resolve(body);
        });
      });
    },
    stageSessionGit: (_agentId: string, _sessionId: string, paths: readonly string[]) =>
      mutate({ kind: 'stage', paths: [...paths] }, () => undefined),
    unstageSessionGit: (_agentId: string, _sessionId: string, paths: readonly string[]) =>
      mutate({ kind: 'unstage', paths: [...paths] }, () => undefined),
    discardSessionGit: (
      _agentId: string,
      _sessionId: string,
      paths: readonly string[],
      scope: SessionGitDiscardScope,
    ) => mutate({ kind: 'discard', paths: [...paths], scope }, () => undefined),
    getSessionGitCommitContext: () =>
      api.commitContext === undefined
        ? Promise.reject(new Error('no commit context staged'))
        : Promise.resolve(api.commitContext),
    commitSessionGit: (_agentId: string, _sessionId: string, request: SessionGitCommitRequest) =>
      mutate({ kind: 'commit', request }, () => {
        if (api.commitResult === undefined) throw new Error('no commit result staged');
        return api.commitResult;
      }),
    startSessionGitJob: (_agentId: string, _sessionId: string, request: SessionGitSyncRequest) => {
      api.starts.push(request);
      if (api.refuseStart !== undefined) return Promise.reject(api.refuseStart);
      const started = api.startResult;
      if (started === undefined) return Promise.reject(new Error('no job staged'));
      if (!api.holdStart) return Promise.resolve(started);
      return new Promise<SessionGitJob>((resolve) => {
        api.releaseStart.push(() => {
          resolve(started);
        });
      });
    },
    getSessionGitJob: (_agentId: string, _sessionId: string, jobId: string) => {
      api.jobPolls.push(jobId);
      if (api.jobError !== undefined) return Promise.reject(api.jobError);
      return api.job === undefined
        ? Promise.reject(new Error('no job staged'))
        : Promise.resolve(api.job);
    },
    startSessionGitRebase: (_agentId: string, _sessionId: string, onto: string) =>
      rebaseCall({ kind: 'start', onto }),
    stepSessionGitRebase: (_agentId: string, _sessionId: string, step: SessionGitRebaseStep) =>
      rebaseCall({ kind: step }),
    getSessionGitConflict: (_agentId: string, _sessionId: string, path: string) => {
      api.conflictReads.push(path);
      return api.conflictFile === undefined
        ? Promise.reject(new Error('no conflict staged'))
        : Promise.resolve(api.conflictFile);
    },
    resolveSessionGitConflict: (
      _agentId: string,
      _sessionId: string,
      request: SessionGitResolveRequest,
    ) => conflictCall({ kind: 'resolve', request }),
    takeSessionGitConflict: (
      _agentId: string,
      _sessionId: string,
      path: string,
      side: SessionGitConflictSide,
    ) => conflictCall({ kind: 'take', path, side }),
    restoreSessionGitConflict: (
      _agentId: string,
      _sessionId: string,
      request: SessionGitRestoreRequest,
    ) => conflictCall({ kind: 'restore', request }),
    cancelSessionGitJob: (_agentId: string, _sessionId: string, jobId: string) => {
      api.cancels.push(jobId);
      if (api.job === undefined) return Promise.reject(new Error('no job staged'));
      return Promise.resolve({
        ...api.job,
        status: 'done' as const,
        ok: false,
        error_code: 'cancelled',
        message: 'cancelled',
      });
    },
  };
});

vi.mock('../../../api/agentCapabilities', () => ({
  fetchAgentCapabilities: () =>
    api.version === undefined
      ? Promise.reject(new Error('no version staged'))
      : Promise.resolve(api.version),
}));

const { toApiError } = await import('../../../api/errors');
const { toSessionGitError } = await import('../../../api/sessionGitRepository');
const { queryKeys } = await import('../../../lib/queryKeys');
const { agentVersionSchema } = await import('../../../protocol/dto/agentVersion');
const { applySessionGitJob } = await import('../../../realtime/sessionGitJobCache');
const { JOB_RESULT_DISMISS_MS } = await import('./sessionGitDisplay');
const { GitChangesPanel } = await import('./GitChangesPanel');

const entry = (over: Partial<SessionGitEntry> = {}): SessionGitEntry => ({
  group: 'unstaged',
  path: 'src/app.py',
  orig_path: null,
  code: 'M',
  index_status: '.',
  worktree_status: 'M',
  rename_score: null,
  submodule: false,
  ...over,
});

const status = (etag: string, entries: SessionGitEntry[]): SessionGitStatus => ({
  session_id: 's1',
  cwd: '/srv/p1',
  etag,
  unchanged: false,
  unavailable_reason: null,
  repo: null,
  entries,
  counts: null,
  truncated: false,
  entry_limit: 5000,
});

const diff = (request: SessionGitDiffRequest): SessionGitDiff => ({
  path: request.path,
  orig_path: request.origPath ?? null,
  scope: request.scope,
  kind: 'text',
  change: 'modified',
  old_mode: '100644',
  new_mode: '100644',
  additions: 0,
  deletions: 0,
  hunks: [],
  truncated: false,
  total_lines: 0,
  line_limit: 5000,
  byte_limit: 1048576,
});

const agentVersion = (git: Record<string, unknown>): AgentVersionInfo =>
  agentVersionSchema.parse({
    name: 'mobaide-agent',
    server_version: '1.4.0',
    protocol_versions: [7],
    min_client_version: 1,
    capabilities: { git },
  });

const ALL_GIT = { changes: true, stage: true, discard: true, commit: true };

/** A failure as the repository throws it off the wire. */
const wireFailure = (httpStatus: number, detail: unknown): unknown =>
  toSessionGitError(toApiError(new Response(null, { status: httpStatus }), { detail }));

let client: QueryClient;
const onClose = vi.fn<() => void>();

const panel = (sessionId = 's1', commitRequest = 0, syncRequest = 0, rebaseRequest = 0) => (
  <QueryClientProvider client={client}>
    <GitChangesPanel
      agentId="a1"
      sessionId={sessionId}
      commitRequest={commitRequest}
      syncRequest={syncRequest}
      rebaseRequest={rebaseRequest}
      onClose={onClose}
    />
  </QueryClientProvider>
);

function renderPanel() {
  return render(panel());
}

/** The one file's row; the badge's tone is part of the accessible name. */
const appRow = (): HTMLElement => screen.getByRole('button', { name: /app\.py/ });
const sectionOf = (row: HTMLElement): HTMLElement => {
  const section = row.closest('section');
  if (section === null) throw new Error('row outside a section');
  return section;
};
const layout = (): HTMLElement => {
  const body = document.querySelector<HTMLElement>('.git-panel-body');
  if (body === null) throw new Error('panel body not rendered');
  return body;
};

/** The next poll's body, then the poll itself through the header button. */
const refreshWith = (next: SessionGitStatus): void => {
  api.status = next;
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
};

/** The same, as the timer would do it: no Refresh button, so no invalidation of its own. */
const pollWith = async (next: SessionGitStatus): Promise<void> => {
  api.status = next;
  await act(async () => {
    await client.invalidateQueries({ queryKey: queryKeys.sessionGitStatus('a1', 's1') });
  });
};

const selectApp = async (): Promise<void> => {
  api.status = status('e1', [entry()]);
  renderPanel();
  fireEvent.click(await screen.findByRole('button', { name: /app\.py/ }));
  await waitFor(() => {
    expect(api.diffs).toHaveLength(1);
  });
};

/** One hunk with an unchanged line naming the file and the context it was asked with. */
const withHunk =
  (change: SessionGitDiff['change']) =>
  (request: SessionGitDiffRequest): Partial<SessionGitDiff> => ({
    change,
    hunks: [
      {
        old_start: 1,
        old_lines: 1,
        new_start: 1,
        new_lines: 2,
        heading: '',
        lines: [
          {
            t: 'ctx',
            old: 1,
            new: 1,
            text: `${request.path} at ${String(request.context)}`,
            no_newline: false,
          },
          { t: 'add', old: null, new: 2, text: 'added line', no_newline: false },
        ],
      },
    ],
  });

const contextGroup = (): HTMLElement | null => screen.queryByRole('group', { name: 'Diff view' });
const diffBody = (path = 'src/app.py'): HTMLElement =>
  screen.getByRole('region', { name: `Changes to ${path}` });
const metaSpinner = (): Element | null => document.querySelector('.git-diff-meta .state-loading');

const releaseDiffs = async (): Promise<void> => {
  await act(async () => {
    for (const release of api.release.splice(0)) release();
    await Promise.resolve();
  });
};

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  api.status = undefined;
  api.diffs.length = 0;
  api.shape = undefined;
  api.hold = false;
  api.release.length = 0;
  // The read-only agent of #669 unless a case says otherwise.
  api.version = agentVersion({ changes: true });
  api.mutations.length = 0;
  api.refuse = undefined;
  api.commitContext = undefined;
  api.commitResult = undefined;
  api.starts.length = 0;
  api.startResult = undefined;
  api.refuseStart = undefined;
  api.holdStart = false;
  api.releaseStart.length = 0;
  api.job = undefined;
  api.jobError = undefined;
  api.jobPolls.length = 0;
  api.cancels.length = 0;
  api.rebases.length = 0;
  api.rebaseResult = undefined;
  api.refuseRebase = undefined;
  api.conflictFile = undefined;
  api.conflictReads.length = 0;
  api.conflictCalls.length = 0;
  api.refuseConflict = undefined;
});

afterEach(() => {
  cleanup();
  client.clear();
  onClose.mockClear();
  vi.useRealTimers();
});

describe('GitChangesPanel selection across polls', () => {
  it('asks for the diff in the scope of the section the file was picked from', async () => {
    await selectApp();

    expect(api.diffs[0]).toMatchObject({ path: 'src/app.py', scope: 'unstaged', context: 3 });
    expect(appRow()).toHaveAttribute('aria-pressed', 'true');
    expect(layout()).toHaveAttribute('data-view', 'diff');
  });

  it('follows the file into Staged and re-reads the diff in the staged scope', async () => {
    // `git add` typed into the PTY beside the panel moves the file between
    // polls; the highlight and the diff's scope must move with it rather than
    // leaving a stale Changes row pressed.
    await selectApp();

    refreshWith(
      status('e2', [
        entry({ group: 'staged', code: 'M', index_status: 'M', worktree_status: '.' }),
      ]),
    );

    await waitFor(() => {
      expect(
        within(sectionOf(appRow())).getByRole('heading', { name: /^Staged/ }),
      ).toBeInTheDocument();
    });
    expect(appRow()).toHaveAttribute('aria-pressed', 'true');
    await waitFor(() => {
      expect(api.diffs.at(-1)).toMatchObject({ path: 'src/app.py', scope: 'staged' });
    });
  });

  it('lets go of a file the next poll no longer lists and returns the stacked view to the list', async () => {
    await selectApp();

    refreshWith(status('e3', []));

    await waitFor(() => {
      expect(screen.getByText('Select a file to see its changes')).toBeInTheDocument();
    });
    expect(screen.getByText('Working tree clean')).toBeInTheDocument();
    expect(screen.queryByRole('button', { pressed: true })).toBeNull();
    expect(layout()).toHaveAttribute('data-view', 'list');
  });

  it('adopts the row a later poll delivers under the same key', async () => {
    // The key survives the poll but the entry behind it is a new object; the
    // diff is built from the entry, so a rename git only detected later must
    // reach the request through the refreshed selection.
    api.status = status('e1', [entry({ group: 'staged', code: 'A', index_status: 'A' })]);
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: /app\.py/ }));
    await waitFor(() => {
      expect(api.diffs).toHaveLength(1);
    });
    expect(api.diffs[0]).not.toHaveProperty('origPath');

    refreshWith(
      status('e2', [
        entry({
          group: 'staged',
          code: 'R',
          index_status: 'R',
          orig_path: 'src/old.py',
          rename_score: 90,
        }),
      ]),
    );

    await waitFor(() => {
      expect(screen.getByText('from src/old.py')).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(api.diffs.at(-1)).toMatchObject({
        path: 'src/app.py',
        scope: 'staged',
        origPath: 'src/old.py',
      });
    });
    expect(appRow()).toHaveAttribute('aria-pressed', 'true');
  });
});

describe('GitChangesPanel diff view control', () => {
  it('waits for the diff before offering Changes / Full file for a modified file', async () => {
    // Whether the choice can matter is read off the response, so there is no
    // control to show while the first answer is still out.
    api.shape = withHunk('modified');
    api.hold = true;
    await selectApp();

    expect(screen.getByText('Loading diff…')).toBeInTheDocument();
    expect(contextGroup()).toBeNull();

    await releaseDiffs();

    const group = await screen.findByRole('group', { name: 'Diff view' });
    expect(within(group).getByRole('button', { name: 'Changes' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(within(group).getByRole('button', { name: 'Full file' })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
    expect(diffBody()).toHaveAttribute('aria-busy', 'false');
  });

  it('offers no diff view choice for an added file, which has no unchanged lines to widen', async () => {
    api.shape = withHunk('added');
    await selectApp();

    await screen.findByText('src/app.py at 3');
    expect(contextGroup()).toBeNull();
  });

  it('keeps the previous diff and its control on screen, marked busy, while the full file loads', async () => {
    api.shape = withHunk('modified');
    await selectApp();
    await screen.findByText('src/app.py at 3');

    const choices = await screen.findByRole('group', { name: 'Diff view' });
    api.hold = true;
    fireEvent.click(within(choices).getByRole('button', { name: 'Full file' }));
    await waitFor(() => {
      expect(api.diffs.at(-1)).toMatchObject({ path: 'src/app.py', context: 5000 });
    });

    expect(screen.queryByText('Loading diff…')).toBeNull();
    expect(screen.getByText('src/app.py at 3')).toBeInTheDocument();
    expect(diffBody()).toHaveAttribute('aria-busy', 'true');
    expect(metaSpinner()).not.toBeNull();
    const group = contextGroup();
    if (group === null) throw new Error('Diff view control dropped while the new diff loads');
    expect(within(group).getByRole('button', { name: 'Full file' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );

    await releaseDiffs();

    await screen.findByText('src/app.py at 5000');
    expect(screen.queryByText('src/app.py at 3')).toBeNull();
    expect(diffBody()).toHaveAttribute('aria-busy', 'false');
    expect(metaSpinner()).toBeNull();
  });

  it('opens another file on its own loading state, not on the previous file\'s diff', async () => {
    // Kept-previous data is scoped to one file: the pane remounts per selection.
    api.shape = withHunk('modified');
    api.status = status('e1', [entry(), entry({ path: 'src/lib.py' })]);
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: /app\.py/ }));
    await screen.findByText('src/app.py at 3');

    api.hold = true;
    fireEvent.click(screen.getByRole('button', { name: /lib\.py/ }));
    await waitFor(() => {
      expect(api.diffs.at(-1)).toMatchObject({ path: 'src/lib.py' });
    });

    expect(screen.getByText('Loading diff…')).toBeInTheDocument();
    expect(screen.queryByText('src/app.py at 3')).toBeNull();
    expect(contextGroup()).toBeNull();
    expect(metaSpinner()).toBeNull();

    await releaseDiffs();

    await screen.findByText('src/lib.py at 3');
    expect(diffBody('src/lib.py')).toHaveAttribute('aria-busy', 'false');
  });

  it('opens the same file in another session on its own loading state, not on the previous session\'s diff', async () => {
    // The terminal view, and the panel with it, survives a route change to an
    // already-attached session, so the selection carries over; its diff must not.
    api.shape = (request, sessionId) =>
      withHunk('modified')({ ...request, path: `${sessionId}/${request.path}` });
    api.status = status('e1', [entry()]);
    const view = render(panel('s1'));
    fireEvent.click(await screen.findByRole('button', { name: /app\.py/ }));
    await screen.findByText('s1/src/app.py at 3');
    expect(contextGroup()).not.toBeNull();

    api.hold = true;
    view.rerender(panel('s2'));
    await waitFor(() => {
      expect(api.diffs).toHaveLength(2);
    });

    expect(api.diffs[1]).toEqual(api.diffs[0]);
    expect(screen.getByText('Loading diff…')).toBeInTheDocument();
    expect(screen.queryByText('s1/src/app.py at 3')).toBeNull();
    expect(contextGroup()).toBeNull();
    expect(metaSpinner()).toBeNull();

    await releaseDiffs();

    await screen.findByText('s2/src/app.py at 3');
    expect(screen.queryByText('s1/src/app.py at 3')).toBeNull();
    expect(diffBody()).toHaveAttribute('aria-busy', 'false');
    expect(contextGroup()).not.toBeNull();
  });
});

describe('GitChangesPanel diff invalidation', () => {
  it('re-reads the open diff from the Refresh button under the session diff prefix', async () => {
    await selectApp();
    const invalidate = vi.spyOn(client, 'invalidateQueries');

    // The same body again: only the button's own invalidation may fire.
    refreshWith(status('e1', [entry()]));

    await waitFor(() => {
      expect(api.diffs).toHaveLength(2);
    });
    expect(api.diffs[1]).toEqual(api.diffs[0]);
    expect(invalidate).toHaveBeenCalledTimes(1);
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ['agent', 'a1', 'session-git', 's1', 'diff'],
    });
    expect(queryKeys.sessionGitDiffs('a1', 's1')).toEqual([
      'agent',
      'a1',
      'session-git',
      's1',
      'diff',
    ]);
  });

  it('re-reads the open diff when a poll brings a new etag for the same file', async () => {
    // A new etag means the working tree moved under the open diff even though
    // the list still names the file the same way.
    await selectApp();

    await pollWith(status('e2', [entry()]));

    await waitFor(() => {
      expect(api.diffs).toHaveLength(2);
    });
    expect(api.diffs[1]).toEqual(api.diffs[0]);
    expect(appRow()).toHaveAttribute('aria-pressed', 'true');
  });
});

const staged = (over: Partial<SessionGitEntry> = {}): SessionGitEntry =>
  entry({ group: 'staged', index_status: 'M', worktree_status: '.', ...over });
const untracked = (path: string): SessionGitEntry =>
  entry({ group: 'untracked', path, code: '?', index_status: '?', worktree_status: '?' });

/** A status whose counts match its entries, as the agent tallies them. */
const counted = (etag: string, entries: SessionGitEntry[]): SessionGitStatus => {
  const tally = { conflicts: 0, staged: 0, unstaged: 0, untracked: 0 };
  for (const item of entries) {
    if (item.group in tally) tally[item.group] += 1;
  }
  return { ...status(etag, entries), counts: { ...tally, total: entries.length } };
};

const capabilitiesSettled = async (): Promise<void> => {
  await waitFor(() => {
    expect(client.getQueryState(queryKeys.agentCapabilities('a1'))?.status).toBe('success');
  });
};

const toolbar = (): HTMLElement => screen.getByRole('toolbar', { name: 'Selected files' });
const tick = (path: string): void => {
  fireEvent.click(screen.getByRole('checkbox', { name: `Select ${path}` }));
};

describe('GitChangesPanel mutating controls', () => {
  it('shows none of them on an agent that only serves the read-only viewer', async () => {
    api.status = status('e1', [entry(), staged({ path: 'src/lib.py' })]);
    renderPanel();
    await screen.findByRole('button', { name: /app\.py/ });
    await capabilitiesSettled();

    expect(screen.queryByRole('button', { name: /^(Stage|Unstage|Discard) / })).toBeNull();
    expect(screen.queryByRole('checkbox')).toBeNull();
    expect(screen.queryByRole('button', { name: /all$/ })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Commit…' })).toBeNull();
  });

  it('offers each row what its section allows, and a conflict nothing', async () => {
    api.version = agentVersion(ALL_GIT);
    api.status = status('e1', [
      entry(),
      staged({ path: 'src/lib.py' }),
      untracked('notes.txt'),
      entry({ group: 'conflicts', path: 'c.py', code: 'UU', index_status: 'U', worktree_status: 'U' }),
    ]);
    renderPanel();

    expect(await screen.findByRole('button', { name: 'Stage src/app.py' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Discard src/app.py' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Unstage src/app.py' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Unstage src/lib.py' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Discard src/lib.py' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Stage notes.txt' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Stage c.py' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Discard c.py' })).toBeNull();
    expect(screen.queryByRole('checkbox', { name: 'Select c.py' })).toBeNull();
    expect(screen.getAllByRole('button', { name: 'Stage all' })).toHaveLength(2);
    expect(screen.getByRole('button', { name: 'Unstage all' })).toBeInTheDocument();
    expect(screen.getAllByRole('checkbox').map((box) => box.getAttribute('aria-label'))).toEqual([
      'Select src/lib.py',
      'Select src/app.py',
      'Select notes.txt',
    ]);
  });

  it('keeps Discard and Commit off an agent that advertises only stage', async () => {
    api.version = agentVersion({ changes: true, stage: true });
    api.status = status('e1', [entry()]);
    renderPanel();

    expect(await screen.findByRole('button', { name: 'Stage src/app.py' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Discard src/app.py' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Commit…' })).toBeNull();
  });
});

describe('GitChangesPanel stage and unstage', () => {
  it('stages a row from its button and re-reads the tree', async () => {
    api.version = agentVersion(ALL_GIT);
    api.status = status('e1', [entry()]);
    renderPanel();
    const stage = await screen.findByRole('button', { name: 'Stage src/app.py' });

    api.status = status('e2', [staged()]);
    fireEvent.click(stage);

    await waitFor(() => {
      expect(api.mutations).toEqual([{ kind: 'stage', paths: ['src/app.py'] }]);
    });
    expect(await screen.findByRole('button', { name: 'Unstage src/app.py' })).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('names both sides of a rename when unstaging it', async () => {
    // `git reset` on the new name alone would leave the deletion staged.
    api.version = agentVersion(ALL_GIT);
    api.status = status('e1', [
      staged({ code: 'R', index_status: 'R', orig_path: 'src/old.py', rename_score: 90 }),
    ]);
    renderPanel();

    fireEvent.click(await screen.findByRole('button', { name: 'Unstage src/app.py' }));

    await waitFor(() => {
      expect(api.mutations).toEqual([{ kind: 'unstage', paths: ['src/app.py', 'src/old.py'] }]);
    });
  });

  it('stages a whole section from its header, and only that section', async () => {
    api.version = agentVersion(ALL_GIT);
    api.status = status('e1', [entry(), entry({ path: 'src/lib.py' }), untracked('notes.txt')]);
    renderPanel();
    const heading = await screen.findByRole('heading', { level: 3, name: /^Changes/ });

    fireEvent.click(within(sectionOf(heading)).getByRole('button', { name: 'Stage all' }));

    await waitFor(() => {
      expect(api.mutations).toEqual([{ kind: 'stage', paths: ['src/app.py', 'src/lib.py'] }]);
    });
  });

  it('reports a refusal inline, keeps the list and lets the notice be dismissed', async () => {
    api.version = agentVersion(ALL_GIT);
    api.status = status('e1', [entry()]);
    api.refuse = (kind) =>
      kind === 'stage'
        ? wireFailure(409, { code: 'index_locked', message: 'index.lock exists' })
        : undefined;
    renderPanel();

    fireEvent.click(await screen.findByRole('button', { name: 'Stage src/app.py' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Another git process is running — try again.');
    expect(screen.getByRole('button', { name: 'Stage src/app.py' })).toBeEnabled();

    fireEvent.click(within(alert).getByRole('button', { name: 'Dismiss' }));

    expect(screen.queryByRole('alert')).toBeNull();
  });
});

describe('GitChangesPanel discard', () => {
  it('asks first, with Cancel focused, then sends the scope of the row', async () => {
    api.version = agentVersion(ALL_GIT);
    api.status = status('e1', [untracked('notes.txt')]);
    renderPanel();

    fireEvent.click(await screen.findByRole('button', { name: 'Discard notes.txt' }));

    const confirm = screen.getByRole('alertdialog', { name: 'Delete untracked file?' });
    expect(within(confirm).getByRole('button', { name: 'Cancel' })).toHaveFocus();
    expect(confirm).toHaveTextContent('notes.txt is not tracked by git and will be deleted.');
    expect(api.mutations).toEqual([]);

    api.status = status('e2', []);
    fireEvent.click(within(confirm).getByRole('button', { name: 'Discard' }));

    await waitFor(() => {
      expect(api.mutations).toEqual([{ kind: 'discard', paths: ['notes.txt'], scope: 'untracked' }]);
    });
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(await screen.findByText('Working tree clean')).toBeInTheDocument();
  });

  it('cancels without a request', async () => {
    api.version = agentVersion(ALL_GIT);
    api.status = status('e1', [entry()]);
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Discard src/app.py' }));
    const confirm = screen.getByRole('alertdialog', { name: 'Discard changes?' });

    fireEvent.click(within(confirm).getByRole('button', { name: 'Cancel' }));

    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(api.mutations).toEqual([]);
    expect(screen.getByRole('button', { name: 'Discard src/app.py' })).toBeInTheDocument();
  });

  it('discards a mixed selection with one request per scope and clears it', async () => {
    api.version = agentVersion(ALL_GIT);
    api.status = status('e1', [entry(), untracked('notes.txt')]);
    renderPanel();
    await screen.findByRole('checkbox', { name: 'Select src/app.py' });
    tick('src/app.py');
    tick('notes.txt');
    expect(toolbar()).toHaveTextContent('2 selected');

    fireEvent.click(within(toolbar()).getByRole('button', { name: 'Discard…' }));
    const confirm = screen.getByRole('alertdialog', { name: 'Discard 2 files?' });
    api.status = status('e2', []);
    fireEvent.click(within(confirm).getByRole('button', { name: 'Discard' }));

    await waitFor(() => {
      expect(api.mutations).toEqual([
        { kind: 'discard', paths: ['src/app.py'], scope: 'unstaged' },
        { kind: 'discard', paths: ['notes.txt'], scope: 'untracked' },
      ]);
    });
    await waitFor(() => {
      expect(screen.queryByRole('toolbar')).toBeNull();
    });
  });
});

describe('GitChangesPanel selection toolbar', () => {
  it('enables each action for the ticked rows it applies to and forgets the rows it acted on', async () => {
    api.version = agentVersion(ALL_GIT);
    api.status = status('e1', [staged({ path: 'src/lib.py' }), entry()]);
    renderPanel();
    await screen.findByRole('checkbox', { name: 'Select src/lib.py' });

    tick('src/lib.py');

    expect(toolbar()).toHaveTextContent('1 selected');
    expect(within(toolbar()).getByRole('button', { name: 'Stage' })).toBeDisabled();
    expect(within(toolbar()).getByRole('button', { name: 'Unstage' })).toBeEnabled();
    expect(within(toolbar()).getByRole('button', { name: 'Discard…' })).toBeDisabled();

    tick('src/app.py');

    expect(toolbar()).toHaveTextContent('2 selected');
    expect(within(toolbar()).getByRole('button', { name: 'Stage' })).toBeEnabled();
    expect(within(toolbar()).getByRole('button', { name: 'Discard…' })).toBeEnabled();

    api.status = status('e2', [entry({ path: 'src/lib.py' }), entry()]);
    fireEvent.click(within(toolbar()).getByRole('button', { name: 'Unstage' }));

    await waitFor(() => {
      expect(api.mutations).toEqual([{ kind: 'unstage', paths: ['src/lib.py'] }]);
    });
    await waitFor(() => {
      expect(toolbar()).toHaveTextContent('1 selected');
    });
    expect(screen.getByRole('checkbox', { name: 'Select src/app.py' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Select src/lib.py' })).not.toBeChecked();
  });

  it('clears the selection from the toolbar', async () => {
    api.version = agentVersion(ALL_GIT);
    api.status = status('e1', [entry()]);
    renderPanel();
    await screen.findByRole('checkbox', { name: 'Select src/app.py' });
    tick('src/app.py');

    fireEvent.click(within(toolbar()).getByRole('button', { name: 'Clear selection' }));

    expect(screen.queryByRole('toolbar')).toBeNull();
    expect(screen.getByRole('checkbox', { name: 'Select src/app.py' })).not.toBeChecked();
  });
});

describe('GitChangesPanel commit', () => {
  const ready: SessionGitCommitContext = {
    author: { name: 'Ada Lovelace', email: 'ada@example.com' },
    head: {
      oid: '4890423520f4dcd158b85374f44fc8819a813ac2',
      short_oid: '4890423',
      subject: 'Fix the login redirect',
      body: 'The callback URL was built before the port was known.',
    },
  };
  const committed: SessionGitCommitResult = {
    oid: 'abc1234def5678abc1234def5678abc1234def56',
    short_oid: 'abc1234',
    subject: 'Add a',
  };

  const summary = (dialog: HTMLElement): HTMLElement => within(dialog).getByLabelText('Summary');
  const description = (dialog: HTMLElement): HTMLElement =>
    within(dialog).getByLabelText('Description');
  const type = (field: HTMLElement, value: string): void => {
    fireEvent.change(field, { target: { value } });
  };

  /** The panel over a staged file, then the sheet through the head button. */
  const openCommit = async (
    entries: SessionGitEntry[] = [staged()],
    context: SessionGitCommitContext = ready,
  ): Promise<HTMLElement> => {
    api.version = agentVersion(ALL_GIT);
    api.status = counted('e1', entries);
    api.commitContext = context;
    api.commitResult = committed;
    renderPanel();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Commit…' })).toBeEnabled();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Commit…' }));
    const dialog = screen.getByRole('dialog', { name: 'Commit' });
    await within(dialog).findByText('Committing as Ada Lovelace <ada@example.com>');
    return dialog;
  };

  it('is disabled while nothing is staged', async () => {
    api.version = agentVersion(ALL_GIT);
    api.status = counted('e1', [entry()]);
    renderPanel();
    await screen.findByRole('button', { name: 'Stage src/app.py' });

    const button = screen.getByRole('button', { name: 'Commit…' });

    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('title', 'Stage files to commit them');
  });

  it('commits the summary and description as one message and reports the new head', async () => {
    const dialog = await openCommit();
    const submit = within(dialog).getByRole('button', { name: 'Commit 1 file' });
    expect(submit).toBeDisabled();

    type(summary(dialog), 'Add a');
    type(description(dialog), 'Why.\n');
    fireEvent.click(within(dialog).getByLabelText('Skip hooks (--no-verify)'));
    expect(submit).toBeEnabled();
    api.status = counted('e2', []);
    fireEvent.click(submit);

    await waitFor(() => {
      expect(api.mutations).toEqual([
        { kind: 'commit', request: { message: 'Add a\n\nWhy.', amend: false, no_verify: true } },
      ]);
    });
    const notice = await screen.findByRole('status');
    expect(notice).toHaveTextContent('Committed abc1234 · Add a');
    expect(screen.queryByRole('dialog', { name: 'Commit' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus();
    expect(await screen.findByText('Working tree clean')).toBeInTheDocument();
  });

  it('submits from the description on Ctrl+Enter', async () => {
    const dialog = await openCommit();
    type(summary(dialog), 'Add a');

    fireEvent.keyDown(description(dialog), { key: 'Enter', ctrlKey: true });

    await waitFor(() => {
      expect(api.mutations).toEqual([
        { kind: 'commit', request: { message: 'Add a', amend: false, no_verify: false } },
      ]);
    });
  });

  it('prefills Amend from the previous commit and takes the prefill back when untoggled untouched', async () => {
    const dialog = await openCommit();
    const amend = within(dialog).getByLabelText('Amend previous commit');

    fireEvent.click(amend);

    expect(summary(dialog)).toHaveValue('Fix the login redirect');
    expect(description(dialog)).toHaveValue(
      'The callback URL was built before the port was known.',
    );
    expect(screen.getByRole('dialog', { name: 'Amend commit' })).toBe(dialog);
    expect(within(dialog).getByRole('button', { name: 'Amend commit' })).toBeEnabled();

    fireEvent.click(amend);

    expect(summary(dialog)).toHaveValue('');
    expect(description(dialog)).toHaveValue('');
    expect(within(dialog).getByRole('button', { name: 'Commit 1 file' })).toBeDisabled();

    fireEvent.click(amend);
    type(summary(dialog), 'Fix the login redirect for real');
    fireEvent.click(amend);

    expect(summary(dialog)).toHaveValue('Fix the login redirect for real');
    expect(description(dialog)).toHaveValue(
      'The callback URL was built before the port was known.',
    );
  });

  it('lets an amend through with nothing staged and opens straight from the terminal menu', async () => {
    api.version = agentVersion(ALL_GIT);
    api.status = counted('e1', [entry()]);
    api.commitContext = ready;
    api.commitResult = { ...committed, subject: 'Fix the login redirect' };
    render(panel('s1', 1));

    const dialog = await screen.findByRole('dialog', { name: 'Commit' });
    await within(dialog).findByText('Committing as Ada Lovelace <ada@example.com>');
    expect(
      within(dialog).getByText(
        'Nothing is staged. Stage files first, or amend the previous commit.',
      ),
    ).toBeInTheDocument();
    type(summary(dialog), 'Nothing to commit');
    expect(within(dialog).getByRole('button', { name: 'Commit 0 files' })).toBeDisabled();

    type(summary(dialog), '');
    fireEvent.click(within(dialog).getByLabelText('Amend previous commit'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Amend commit' }));

    await waitFor(() => {
      expect(api.mutations).toEqual([
        {
          kind: 'commit',
          request: {
            message:
              'Fix the login redirect\n\nThe callback URL was built before the port was known.',
            amend: true,
            no_verify: false,
          },
        },
      ]);
    });
    expect(await screen.findByRole('status')).toHaveTextContent(
      'Committed abc1234 · Fix the login redirect',
    );
  });

  it('shows the host-side fix and refuses to submit when git has no identity', async () => {
    api.version = agentVersion(ALL_GIT);
    api.status = counted('e1', [staged()]);
    api.commitContext = { author: null, head: null };
    renderPanel();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Commit…' })).toBeEnabled();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Commit…' }));
    const dialog = screen.getByRole('dialog', { name: 'Commit' });

    const alert = await within(dialog).findByRole('alert');

    expect(alert).toHaveTextContent("Git doesn't know who you are on the agent machine.");
    expect(alert).toHaveTextContent('git config --global user.name "Your Name"');
    expect(alert).toHaveTextContent('git config --global user.email "you@example.com"');
    expect(within(dialog).queryByLabelText('Amend previous commit')).toBeNull();
    type(summary(dialog), 'Who am I');
    expect(within(dialog).getByRole('button', { name: 'Commit 1 file' })).toBeDisabled();
  });

  it('keeps a hook refusal inline with the hook output and the typed message', async () => {
    api.refuse = (kind) =>
      kind === 'commit' ? wireFailure(409, { code: 'hook_failed', message: 'lint: nope' }) : undefined;
    const dialog = await openCommit();
    type(summary(dialog), 'Add a');

    fireEvent.click(within(dialog).getByRole('button', { name: 'Commit 1 file' }));

    const alert = await within(dialog).findByRole('alert');
    expect(alert).toHaveTextContent('A commit hook rejected the commit.');
    expect(alert.querySelector('pre')).toHaveTextContent('lint: nope');
    expect(screen.getByRole('dialog', { name: 'Commit' })).toBeInTheDocument();
    expect(summary(dialog)).toHaveValue('Add a');
    expect(screen.queryByRole('status')).toBeNull();
  });
});

describe('GitChangesPanel sync', () => {
  const ALL_SYNC = { ...ALL_GIT, fetch: true, pull: true, push: true };
  const UPSTREAM = '↑1 ↓0 · origin/main';

  /** A status with a repo on `main`, one commit ahead of `origin/main`. */
  const tracked = (etag: string, entries: SessionGitEntry[]): SessionGitStatus => ({
    ...counted(etag, entries),
    repo: {
      toplevel: '/srv/p1',
      git_dir: '/srv/p1/.git',
      common_dir: '/srv/p1/.git',
      head: { oid: 'abc1234def', short_oid: 'abc1234', branch: 'main', detached: false, initial: false },
      upstream: { ref: 'origin/main', ahead: 1, behind: 0 },
      state: 'clean',
      is_linked_worktree: false,
    },
  });

  const running = (over: Partial<SessionGitJob> = {}): SessionGitJob => ({
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

  const sheet = (): HTMLElement => screen.getByRole('dialog', { name: 'Fetch / Pull / Push' });
  const sheetRow = (label: string): HTMLElement => {
    const button = within(sheet())
      .getByText(label, { selector: '.git-sync-row-label' })
      .closest('button');
    if (button === null) throw new Error(`no row labelled ${label}`);
    return button;
  };
  const banner = (): HTMLElement | null => document.querySelector<HTMLElement>('.git-panel-job');
  /** A job-route failure as the repository throws it, typed for the mock's reject. */
  const jobFailure = (httpStatus: number, detail: unknown): Error => {
    const error = wireFailure(httpStatus, detail);
    if (!(error instanceof Error)) throw new Error('expected an Error off the wire');
    return error;
  };
  const shownBanner = (): HTMLElement => {
    const strip = banner();
    if (strip === null) throw new Error('no job banner');
    return strip;
  };

  /** The panel over a tracked repo on a sync-capable agent, Sync… ready. */
  const renderSync = async (syncRequest = 0): Promise<void> => {
    api.version = agentVersion(ALL_SYNC);
    api.status = tracked('e1', [entry()]);
    render(panel('s1', 0, syncRequest));
    await screen.findByRole('button', { name: 'Sync…' });
  };

  /** Open the sheet, start a push from it and wait for the request to leave. */
  const startPush = async (): Promise<void> => {
    fireEvent.click(screen.getByRole('button', { name: 'Sync…' }));
    fireEvent.click(sheetRow('Push'));
    await waitFor(() => {
      expect(api.starts).toEqual([{ op: 'push', force: false }]);
    });
  };

  it('offers Sync… and the tappable upstream only once the agent advertises a job', async () => {
    api.version = agentVersion(ALL_GIT);
    api.status = tracked('e1', [entry()]);
    renderPanel();
    await screen.findByRole('button', { name: 'Stage src/app.py' });
    await capabilitiesSettled();

    expect(screen.queryByRole('button', { name: 'Sync…' })).toBeNull();
    expect(screen.queryByRole('button', { name: UPSTREAM })).toBeNull();
    expect(screen.getByText(UPSTREAM)).toBeInTheDocument();
  });

  it('opens the sheet from the upstream line and hands focus to Close when it shuts', async () => {
    await renderSync();

    fireEvent.click(screen.getByRole('button', { name: UPSTREAM }));

    expect(within(sheet()).getByText(UPSTREAM)).toBeInTheDocument();
    expect(sheetRow('Push')).toHaveTextContent('1 commit to origin/main');
    expect(sheetRow('Pull')).toBeDisabled();

    fireEvent.click(within(sheet()).getByRole('button', { name: 'Close' }));

    expect(screen.queryByRole('dialog', { name: 'Fetch / Pull / Push' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus();
    expect(api.starts).toEqual([]);
  });

  it('opens the sheet straight from the terminal menu', async () => {
    await renderSync(1);

    expect(screen.getByRole('dialog', { name: 'Fetch / Pull / Push' })).toBeInTheDocument();
  });

  it('keeps Escape for the sheet while it is open', async () => {
    await renderSync();
    fireEvent.click(screen.getByRole('button', { name: 'Sync…' }));

    fireEvent.keyDown(screen.getByRole('dialog', { name: 'Git changes' }), { key: 'Escape' });

    expect(onClose).not.toHaveBeenCalled();
  });

  it('starts a push, shows the banner from the first snapshot and follows the poll', async () => {
    await renderSync();
    api.startResult = running();
    api.job = running({
      phase: 'Writing objects',
      percent: 45,
      line: 'Writing objects:  45% (14/32)',
    });

    await startPush();

    await waitFor(() => {
      expect(shownBanner()).toHaveTextContent('Pushing…');
    });
    await waitFor(() => {
      expect(within(shownBanner()).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '45');
    });
    expect(api.jobPolls).toEqual(['j1']);
    expect(shownBanner()).toHaveAttribute('data-tone', 'busy');
    expect(within(shownBanner()).getByRole('button', { name: 'Cancel' })).toBeEnabled();
    expect(within(shownBanner()).queryByRole('button', { name: 'Dismiss' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Stage src/app.py' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeEnabled();
    expect(within(sheet()).getByRole('status')).toHaveTextContent('Writing objects 45%');

    fireEvent.click(within(sheet()).getByRole('button', { name: 'Close' }));

    expect(screen.queryByRole('dialog', { name: 'Fetch / Pull / Push' })).toBeNull();
    expect(shownBanner()).toHaveTextContent('Pushing…');
  });

  it('keeps the staged set off limits from the start request until its answer', async () => {
    await renderSync();
    api.holdStart = true;
    api.startResult = running({ status: 'done', ok: true, message: 'Everything up-to-date' });
    fireEvent.click(screen.getByRole('button', { name: 'Sync…' }));
    fireEvent.click(sheetRow('Push'));
    await waitFor(() => {
      expect(api.starts).toEqual([{ op: 'push', force: false }]);
    });

    expect(banner()).toBeNull();
    expect(screen.getByRole('button', { name: 'Stage src/app.py' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeEnabled();

    await act(async () => {
      for (const release of api.releaseStart.splice(0)) release();
      await Promise.resolve();
    });

    await waitFor(() => {
      expect(shownBanner()).toHaveTextContent('Pushed · Everything up-to-date');
    });
    expect(api.jobPolls).toEqual([]);
    expect(screen.getByRole('button', { name: 'Stage src/app.py' })).toBeEnabled();
  });

  it('applies a socket frame to the banner and lets a success clear itself after six seconds', async () => {
    await renderSync();
    api.startResult = running();
    api.job = running();
    await startPush();
    await waitFor(() => {
      expect(shownBanner()).toHaveTextContent('Pushing…');
    });
    vi.useFakeTimers();

    act(() => {
      applySessionGitJob(
        client,
        'a1',
        running({ status: 'done', ok: true, percent: 100, message: 'abc1234..def5678 main -> main' }),
      );
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });

    expect(shownBanner()).toHaveTextContent('Pushed · abc1234..def5678 main -> main');
    expect(shownBanner()).toHaveAttribute('data-tone', 'ok');
    expect(screen.getByRole('button', { name: 'Stage src/app.py' })).toBeEnabled();
    expect(within(shownBanner()).getByRole('button', { name: 'Dismiss' })).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(JOB_RESULT_DISMISS_MS + 5);
    });

    expect(banner()).toBeNull();
    expect(api.jobPolls).toEqual(['j1']);
  });

  it('keeps a failed job on screen with Options… back to the sheet and its follow-up', async () => {
    await renderSync();
    api.startResult = running();
    api.job = running({
      status: 'done',
      ok: false,
      error_code: 'rejected',
      message: '! [rejected] main -> main (fetch first)',
    });
    await startPush();
    await waitFor(() => {
      expect(shownBanner()).toHaveTextContent('Push failed: The remote rejected the push.');
    });
    expect(shownBanner()).toHaveAttribute('data-tone', 'danger');
    expect(shownBanner()).toHaveAttribute('role', 'alert');
    fireEvent.click(within(sheet()).getByRole('button', { name: 'Close' }));

    fireEvent.click(within(shownBanner()).getByRole('button', { name: 'Options…' }));

    expect(
      within(sheet()).getByRole('button', { name: 'Force push (with lease)…' }),
    ).toBeInTheDocument();
    fireEvent.click(within(sheet()).getByRole('button', { name: 'Close' }));

    fireEvent.click(within(shownBanner()).getByRole('button', { name: 'Dismiss' }));

    expect(banner()).toBeNull();
  });

  it('adopts the job a 409 job_running names instead of failing', async () => {
    await renderSync();
    api.refuseStart = jobFailure(409, { code: 'job_running', message: 'j9' });
    api.job = running({ job_id: 'j9', op: 'fetch', phase: 'Receiving objects', percent: 12 });
    fireEvent.click(screen.getByRole('button', { name: 'Sync…' }));

    fireEvent.click(sheetRow('Fetch'));

    await waitFor(() => {
      expect(api.jobPolls).toEqual(['j9']);
    });
    expect(within(sheet()).getByRole('alert')).toHaveTextContent(
      'Another fetch, pull or push is still running.',
    );
    await waitFor(() => {
      expect(shownBanner()).toHaveTextContent('Fetching…');
    });
  });

  it('adopts a job another client started from the socket pointer', async () => {
    await renderSync();
    api.job = running({ job_id: 'j5', op: 'pull' });

    act(() => {
      applySessionGitJob(client, 'a1', running({ job_id: 'j5', op: 'pull' }));
    });

    await waitFor(() => {
      expect(shownBanner()).toHaveTextContent('Pulling…');
    });
    expect(api.jobPolls).toEqual(['j5']);
    expect(screen.getByRole('button', { name: 'Stage src/app.py' })).toBeDisabled();
  });

  it('cancels from the banner and shows the cancelled outcome', async () => {
    await renderSync();
    api.startResult = running();
    api.job = running({ phase: 'Writing objects', percent: 45 });
    await startPush();
    await waitFor(() => {
      expect(within(shownBanner()).getByRole('button', { name: 'Cancel' })).toBeEnabled();
    });

    fireEvent.click(within(shownBanner()).getByRole('button', { name: 'Cancel' }));

    await waitFor(() => {
      expect(api.cancels).toEqual(['j1']);
    });
    await waitFor(() => {
      expect(shownBanner()).toHaveTextContent('Push failed: Cancelled.');
    });
    expect(screen.getByRole('button', { name: 'Stage src/app.py' })).toBeEnabled();
  });

  it('reports a job the agent no longer knows as lost, and lets the notice go', async () => {
    await renderSync();
    api.jobError = jobFailure(404, { code: 'job_not_found' });

    act(() => {
      applySessionGitJob(client, 'a1', running({ job_id: 'j8' }));
    });

    await waitFor(() => {
      expect(shownBanner()).toHaveTextContent(
        'Lost track of the job — refresh to see the result.',
      );
    });
    expect(api.jobPolls).toEqual(['j8']);
    // Android `GitJobBanner.running` and iOS `GitJobBanner.running` are both
    // `!lost && …`: a job the agent forgot holds nothing, so the staged set
    // is back in reach while the notice is up.
    const stageWhileLost = screen.getByRole('button', { name: 'Stage src/app.py' }).hasAttribute(
      'disabled',
    );

    fireEvent.click(within(shownBanner()).getByRole('button', { name: 'Dismiss' }));

    expect(banner()).toBeNull();
    expect(screen.getByRole('button', { name: 'Stage src/app.py' })).toBeEnabled();
    expect(stageWhileLost).toBe(false);
  });
});

describe('GitChangesPanel rebase', () => {
  const ALL_REBASE = { ...ALL_GIT, rebase: true, resolve: true };

  const info = (over: Partial<SessionGitRebaseInfo> = {}): SessionGitRebaseInfo => ({
    branch: 'topic',
    onto_oid: '89abcdef0123456789abcdef0123456789abcdef',
    onto_short_oid: '89abcde',
    onto_ref: 'main',
    stopped_on: { oid: 'fedcba98', short_oid: 'fedcba9', subject: 'Fix the login redirect' },
    step: 2,
    total: 5,
    ...over,
  });

  const conflict = (path: string): SessionGitEntry =>
    entry({ group: 'conflicts', path, code: 'UU', index_status: 'U', worktree_status: 'U' });

  /**
   * A linked-worktree session stopped mid-rebase: HEAD detached, the branch in `rebase`.
   * `null` details are what a `git am` session reports.
   */
  const rebasing = (
    etag: string,
    entries: SessionGitEntry[],
    over: Partial<SessionGitRebaseInfo> | null = {},
  ): SessionGitStatus => ({
    ...counted(etag, entries),
    repo: {
      toplevel: '/srv/p1',
      git_dir: '/srv/p1/.git',
      common_dir: '/srv/p1/.git',
      head: { oid: '89abcdef', short_oid: '89abcde', branch: null, detached: true, initial: false },
      upstream: null,
      state: 'rebasing',
      is_linked_worktree: false,
      rebase: over === null ? null : info(over),
    },
  });

  /** The same repository once the rebase is over, back on `topic`. */
  const onTopic = (etag: string, entries: SessionGitEntry[] = []): SessionGitStatus => ({
    ...counted(etag, entries),
    repo: {
      toplevel: '/srv/p1',
      git_dir: '/srv/p1/.git',
      common_dir: '/srv/p1/.git',
      head: { oid: 'abc1234def', short_oid: 'abc1234', branch: 'topic', detached: false, initial: false },
      upstream: null,
      state: 'clean',
      is_linked_worktree: false,
      rebase: null,
    },
  });

  /** A rebase-route failure as the repository throws it, typed for the mock's reject. */
  const rebaseFailure = (httpStatus: number, detail: unknown): Error => {
    const error = wireFailure(httpStatus, detail);
    if (!(error instanceof Error)) throw new Error('expected an Error off the wire');
    return error;
  };

  const banner = (): HTMLElement | null => document.querySelector<HTMLElement>('.git-panel-rebase');
  const shownBanner = (): HTMLElement => {
    const strip = banner();
    if (strip === null) throw new Error('no rebase banner');
    return strip;
  };
  const notice = (): HTMLElement | null =>
    document.querySelector<HTMLElement>('.git-panel-notice:not(.git-panel-rebase):not(.git-panel-job)');

  const renderRebasing = async (next: SessionGitStatus, rebaseRequest = 0): Promise<void> => {
    api.version = agentVersion(ALL_REBASE);
    api.status = next;
    render(panel('s1', 0, 0, rebaseRequest));
    await waitFor(() => {
      expect(shownBanner()).toBeInTheDocument();
    });
    await capabilitiesSettled();
  };

  it('shows where the rebase stopped, holds Continue while conflicts remain and offers Mark resolved', async () => {
    await renderRebasing(rebasing('e1', [conflict('src/login.py')]));

    expect(shownBanner()).toHaveTextContent('Rebasing topic onto main');
    expect(shownBanner()).toHaveTextContent('Stopped at fedcba9 Fix the login redirect · 2/5');
    expect(shownBanner()).toHaveTextContent('1 conflict — fix the files, then Mark resolved.');
    const proceed = within(shownBanner()).getByRole('button', { name: 'Continue' });
    expect(proceed).toBeDisabled();
    expect(proceed).toHaveAttribute('title', 'Mark every conflict resolved first');
    expect(within(shownBanner()).getByRole('button', { name: 'Skip…' })).toBeEnabled();
    expect(within(shownBanner()).getByRole('button', { name: 'Abort…' })).toBeEnabled();
    expect(await screen.findByRole('button', { name: 'Mark src/login.py resolved' })).toBeEnabled();
    expect(screen.queryByRole('checkbox', { name: 'Select src/login.py' })).toBeNull();
  });

  it('marks a conflicted file resolved through the stage route and re-reads the tree', async () => {
    await renderRebasing(rebasing('e1', [conflict('src/login.py')]));
    const resolve = await screen.findByRole('button', { name: 'Mark src/login.py resolved' });

    api.status = rebasing('e2', [entry({ group: 'staged', path: 'src/login.py', index_status: 'M', worktree_status: '.' })]);
    fireEvent.click(resolve);

    await waitFor(() => {
      expect(api.mutations).toEqual([{ kind: 'stage', paths: ['src/login.py'] }]);
    });
    await waitFor(() => {
      expect(within(shownBanner()).getByRole('button', { name: 'Continue' })).toBeEnabled();
    });
    expect(shownBanner()).toHaveTextContent('No conflicts left — Continue when ready.');
  });

  it('marks the file resolved from the diff pane too', async () => {
    await renderRebasing(rebasing('e1', [conflict('src/login.py')]));

    fireEvent.click(await screen.findByRole('button', { name: /login\.py/, pressed: false }));
    fireEvent.click(await screen.findByRole('button', { name: 'Mark resolved' }));

    await waitFor(() => {
      expect(api.mutations).toEqual([{ kind: 'stage', paths: ['src/login.py'] }]);
    });
  });

  it('names the file that still holds conflict markers when the agent refuses', async () => {
    await renderRebasing(rebasing('e1', [conflict('src/login.py')]));
    api.refuse = (kind) =>
      kind === 'stage' ? wireFailure(409, { code: 'conflict_markers', message: 'src/login.py' }) : undefined;

    fireEvent.click(await screen.findByRole('button', { name: 'Mark src/login.py resolved' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'src/login.py still has conflict markers — finish resolving it first.',
    );
  });

  it('continues once nothing is left to resolve and reports the finished rebase', async () => {
    await renderRebasing(rebasing('e1', []));
    api.rebaseResult = { outcome: 'rebased', rebase: null, message: null };
    api.status = onTopic('e2');

    fireEvent.click(within(shownBanner()).getByRole('button', { name: 'Continue' }));

    await waitFor(() => {
      expect(api.rebases).toEqual([{ kind: 'continue' }]);
    });
    await waitFor(() => {
      expect(notice()).toHaveTextContent('Rebase finished.');
    });
    await waitFor(() => {
      expect(banner()).toBeNull();
    });
    expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus();
  });

  it('asks before skipping, with Cancel focused, and reports the next stop', async () => {
    await renderRebasing(rebasing('e1', [conflict('src/login.py')]));
    api.rebaseResult = {
      outcome: 'stopped',
      rebase: info({ stopped_on: { oid: 'aa', short_oid: 'aa11bb2', subject: 'Tidy the callback' }, step: 3 }),
      message: null,
    };

    fireEvent.click(within(shownBanner()).getByRole('button', { name: 'Skip…' }));
    const confirm = screen.getByRole('alertdialog', { name: 'Skip this commit?' });
    expect(confirm).toHaveTextContent('fedcba9 “Fix the login redirect” will be left out of topic.');
    expect(within(confirm).getByRole('button', { name: 'Cancel' })).toHaveFocus();

    fireEvent.click(within(confirm).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(api.rebases).toEqual([]);

    fireEvent.click(within(shownBanner()).getByRole('button', { name: 'Skip…' }));
    fireEvent.click(
      within(screen.getByRole('alertdialog', { name: 'Skip this commit?' })).getByRole('button', {
        name: 'Skip commit',
      }),
    );

    await waitFor(() => {
      expect(api.rebases).toEqual([{ kind: 'skip' }]);
    });
    await waitFor(() => {
      expect(notice()).toHaveTextContent(
        'Rebase stopped at “Tidy the callback” — resolve the conflicts, then Continue.',
      );
    });
    expect(notice()).toHaveAttribute('data-tone', 'danger');
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('aborts after confirmation and names the branch it restored', async () => {
    await renderRebasing(rebasing('e1', [conflict('src/login.py')]));
    api.rebaseResult = { outcome: 'aborted', rebase: null, message: null };

    fireEvent.click(within(shownBanner()).getByRole('button', { name: 'Abort…' }));
    const confirm = screen.getByRole('alertdialog', { name: 'Abort the rebase?' });
    expect(confirm).toHaveTextContent(
      'topic goes back to where it was before the rebase. Conflict resolutions made so far are discarded.',
    );
    api.status = onTopic('e2');
    fireEvent.click(within(confirm).getByRole('button', { name: 'Abort rebase' }));

    await waitFor(() => {
      expect(api.rebases).toEqual([{ kind: 'abort' }]);
    });
    await waitFor(() => {
      expect(notice()).toHaveTextContent('Rebase aborted — topic is back where it started.');
    });
    await waitFor(() => {
      expect(banner()).toBeNull();
    });
    expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus();
  });

  it('reports a refused step with the rebase copy', async () => {
    await renderRebasing(rebasing('e1', []));
    api.refuseRebase = rebaseFailure(409, {
      code: 'dirty_worktree',
      message: 'unstaged changes to tracked files',
    });

    fireEvent.click(within(shownBanner()).getByRole('button', { name: 'Continue' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Local changes are in the way — stage, commit or discard them first.',
    );
    expect(shownBanner()).toBeInTheDocument();
  });

  it('keeps Escape for the step confirmation while it is open', async () => {
    await renderRebasing(rebasing('e1', []));
    fireEvent.click(within(shownBanner()).getByRole('button', { name: 'Abort…' }));

    fireEvent.keyDown(screen.getByRole('dialog', { name: 'Git changes' }), { key: 'Escape' });

    expect(onClose).not.toHaveBeenCalled();
  });

  it('shows the banner without controls on an agent that cannot rebase', async () => {
    api.version = agentVersion(ALL_GIT);
    api.status = rebasing('e1', [conflict('src/login.py')]);
    renderPanel();
    await waitFor(() => {
      expect(shownBanner()).toHaveTextContent('Rebasing topic onto main');
    });
    await capabilitiesSettled();

    expect(within(shownBanner()).queryByRole('button')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Mark src/login.py resolved' })).toBeNull();
  });

  it('points to the terminal instead of Continue, Skip and Abort when the agent reports no rebase details', async () => {
    await renderRebasing(rebasing('e1', [], null));

    expect(shownBanner()).toHaveTextContent('Rebase in progress');
    expect(shownBanner()).toHaveTextContent('Continue, skip or abort it in the terminal.');
    expect(within(shownBanner()).queryByRole('button')).toBeNull();
  });

  it('opens the sheet from the terminal menu and reports the rebase it started', async () => {
    api.version = agentVersion(ALL_REBASE);
    api.status = onTopic('e1');
    api.rebaseResult = { outcome: 'rebased', rebase: null, message: null };
    render(panel('s1', 0, 0, 1));

    const sheet = await screen.findByRole('dialog', { name: 'Rebase topic' });
    fireEvent.change(within(sheet).getByLabelText('Onto'), { target: { value: 'main' } });
    await waitFor(() => {
      expect(within(sheet).getByRole('button', { name: 'Rebase' })).toBeEnabled();
    });
    fireEvent.click(within(sheet).getByRole('button', { name: 'Rebase' }));

    await waitFor(() => {
      expect(api.rebases).toEqual([{ kind: 'start', onto: 'main' }]);
    });
    await waitFor(() => {
      expect(notice()).toHaveTextContent('Rebased topic onto main.');
    });
    expect(screen.queryByRole('dialog', { name: 'Rebase topic' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus();
  });
});

describe('GitChangesPanel conflict resolver', () => {
  const ALL_CONFLICTS = { ...ALL_GIT, rebase: true, resolve: true, conflicts: true };
  const CONFLICTED = 'a\n<<<<<<< HEAD\nmain\n=======\ntopic\n>>>>>>> fedcba9 (Fix the login redirect)\n';

  const conflictEntry = (path: string): SessionGitEntry =>
    entry({ group: 'conflicts', path, code: 'UU', index_status: 'U', worktree_status: 'U' });

  const file = (over: Partial<SessionGitConflict> = {}): SessionGitConflict => ({
    path: 'src/login.py',
    code: 'UU',
    state: 'rebasing',
    kind: 'text',
    content: CONFLICTED,
    token: 't1',
    markers: 1,
    has_base: false,
    ours: { label: 'main', present: true },
    theirs: { label: 'fedcba9 Fix the login redirect', present: true },
    byte_limit: 262144,
    ...over,
  });

  /** Stopped mid-rebase, with the paths the agent can still restore. */
  const stopped = (
    etag: string,
    entries: SessionGitEntry[],
    resolvedPaths: string[] = [],
  ): SessionGitStatus => ({
    ...counted(etag, entries),
    resolved_paths: resolvedPaths,
    repo: {
      toplevel: '/srv/p1',
      git_dir: '/srv/p1/.git',
      common_dir: '/srv/p1/.git',
      head: { oid: '89abcdef', short_oid: '89abcde', branch: null, detached: true, initial: false },
      upstream: null,
      state: 'rebasing',
      is_linked_worktree: false,
      rebase: {
        branch: 'topic',
        onto_oid: '89abcdef0123456789abcdef0123456789abcdef',
        onto_short_oid: '89abcde',
        onto_ref: 'main',
        stopped_on: { oid: 'fedcba98', short_oid: 'fedcba9', subject: 'Fix the login redirect' },
        step: 2,
        total: 5,
      },
    },
  });

  const failure = (httpStatus: number, detail: unknown): Error => {
    const error = wireFailure(httpStatus, detail);
    if (!(error instanceof Error)) throw new Error('expected an Error off the wire');
    return error;
  };

  const editor = (): HTMLTextAreaElement =>
    screen.getByRole<HTMLTextAreaElement>('textbox', { name: 'Contents of src/login.py' });
  const queryEditor = (): HTMLElement | null =>
    screen.queryByRole('textbox', { name: 'Contents of src/login.py' });
  const type = (text: string): void => {
    fireEvent.input(editor(), { target: { value: text } });
  };
  const notice = (): HTMLElement | null =>
    document.querySelector<HTMLElement>('.git-panel-notice:not(.git-panel-rebase):not(.git-panel-job)');
  /** The modified file's row itself: its Stage and Discard buttons carry the path too. */
  const otherRow = (): HTMLElement => screen.getByRole('button', { name: /app\.py/, pressed: false });

  const renderStopped = async (
    next: SessionGitStatus,
    git: Record<string, unknown> = ALL_CONFLICTS,
  ): Promise<void> => {
    api.version = agentVersion(git);
    api.status = next;
    api.conflictFile = file();
    renderPanel();
    await screen.findByText('Rebasing topic onto main');
    await capabilitiesSettled();
  };

  /** The resolver open on `src/login.py`, beside an ordinary modified file. */
  const openResolver = async (): Promise<void> => {
    await renderStopped(stopped('e1', [conflictEntry('src/login.py'), entry()]));
    fireEvent.click(screen.getByRole('button', { name: /login\.py/, pressed: false }));
    await waitFor(() => {
      expect(queryEditor()).not.toBeNull();
    });
  };

  it('opens a conflict row in the resolver, not in a diff, and says so in the banner', async () => {
    await openResolver();

    expect(editor().value).toBe(CONFLICTED);
    expect(api.conflictReads).toEqual(['src/login.py']);
    expect(api.diffs).toEqual([]);
    expect(document.querySelector('.git-panel-rebase')).toHaveTextContent(
      '1 conflict — open a file to resolve it.',
    );
    expect(screen.getByRole('button', { name: 'Continue' })).toHaveAttribute(
      'title',
      'Resolve every conflict first',
    );
  });

  it('keeps the HEAD diff, with no Resolve… button, on an agent without the resolver', async () => {
    await renderStopped(stopped('e1', [conflictEntry('src/login.py')]), {
      ...ALL_GIT,
      rebase: true,
      resolve: true,
    });

    fireEvent.click(screen.getByRole('button', { name: /login\.py/, pressed: false }));

    await waitFor(() => {
      expect(api.diffs).toHaveLength(1);
    });
    expect(api.conflictReads).toEqual([]);
    expect(queryEditor()).toBeNull();
    expect(screen.queryByRole('button', { name: 'Resolve…' })).toBeNull();
    expect(document.querySelector('.git-panel-rebase')).toHaveTextContent(
      '1 conflict — fix the files, then Mark resolved.',
    );
  });

  it('goes to the HEAD diff from the menu and back through Resolve…', async () => {
    await openResolver();

    fireEvent.click(screen.getByRole('button', { name: 'More conflict actions' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'View diff' }));

    await waitFor(() => {
      expect(api.diffs.map((request) => [request.path, request.scope])).toEqual([
        ['src/login.py', 'unstaged'],
      ]);
    });
    expect(queryEditor()).toBeNull();

    fireEvent.click(await screen.findByRole('button', { name: 'Resolve…' }));

    await waitFor(() => {
      expect(queryEditor()).not.toBeNull();
    });
    expect(api.conflictReads).toEqual(['src/login.py', 'src/login.py']);
  });

  it('closes on Apply, reports the resolved file and hands focus back to Close', async () => {
    await openResolver();
    type('a\nmerged\n');
    const apply = screen.getByRole('button', { name: 'Apply & mark resolved' });
    await waitFor(() => {
      expect(apply).toBeEnabled();
    });
    api.status = stopped('e2', [entry()], ['src/login.py']);

    fireEvent.click(apply);

    await waitFor(() => {
      expect(notice()).toHaveTextContent('Resolved src/login.py.');
    });
    expect(api.conflictCalls).toEqual([
      {
        kind: 'resolve',
        request: { path: 'src/login.py', token: 't1', content: 'a\nmerged\n', markResolved: true },
      },
    ]);
    expect(queryEditor()).toBeNull();
    expect(layout()).toHaveAttribute('data-view', 'list');
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus();
    });
  });

  describe('with unsaved edits', () => {
    const dirtyResolver = async (): Promise<void> => {
      await openResolver();
      type('half done\n');
    };

    it('asks before another row replaces the editor, with Keep editing focused', async () => {
      await dirtyResolver();

      fireEvent.click(otherRow());

      const confirm = screen.getByRole('alertdialog', { name: 'Discard your edits?' });
      expect(confirm).toHaveTextContent('Your changes to login.py have not been saved.');
      expect(within(confirm).getByRole('button', { name: 'Keep editing' })).toHaveFocus();
      expect(api.diffs).toEqual([]);

      fireEvent.click(within(confirm).getByRole('button', { name: 'Keep editing' }));

      expect(screen.queryByRole('alertdialog')).toBeNull();
      expect(editor().value).toBe('half done\n');
      expect(api.diffs).toEqual([]);
    });

    it('switches to the other row once the edits are given up', async () => {
      await dirtyResolver();
      fireEvent.click(otherRow());

      fireEvent.click(screen.getByRole('button', { name: 'Discard' }));

      await waitFor(() => {
        expect(api.diffs.map((request) => request.path)).toEqual(['src/app.py']);
      });
      expect(queryEditor()).toBeNull();
    });

    it.each([
      ['Escape', () => fireEvent.keyDown(layout(), { key: 'Escape' })],
      ['the Close button', () => fireEvent.click(screen.getByRole('button', { name: 'Close' }))],
    ])('asks before %s closes the panel, and closes only on Discard', async (_name, leave) => {
      await dirtyResolver();

      leave();

      const confirm = screen.getByRole('alertdialog', { name: 'Discard your edits?' });
      expect(onClose).not.toHaveBeenCalled();

      fireEvent.click(within(confirm).getByRole('button', { name: 'Discard' }));

      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('asks before View diff leaves the editor', async () => {
      await dirtyResolver();

      fireEvent.click(screen.getByRole('button', { name: 'More conflict actions' }));
      fireEvent.click(screen.getByRole('menuitem', { name: 'View diff' }));

      expect(screen.getByRole('alertdialog', { name: 'Discard your edits?' })).toBeInTheDocument();
      expect(queryEditor()).not.toBeNull();
    });

    it('takes a click on its own row as no way out: no question, the draft kept and still guarded', async () => {
      await dirtyResolver();

      fireEvent.click(screen.getByRole('button', { name: /login\.py/, pressed: true }));

      expect(screen.queryByRole('alertdialog')).toBeNull();
      expect(editor().value).toBe('half done\n');
      expect(layout()).toHaveAttribute('data-view', 'diff');
      expect(api.conflictReads).toEqual(['src/login.py']);
      expect(api.diffs).toEqual([]);

      fireEvent.keyDown(layout(), { key: 'Escape' });

      expect(screen.getByRole('alertdialog', { name: 'Discard your edits?' })).toBeInTheDocument();
      expect(onClose).not.toHaveBeenCalled();
    });

    it('stays open, and says why, when the file leaves Conflicts underneath it', async () => {
      await dirtyResolver();

      await pollWith(stopped('e2', [entry()], ['src/login.py']));

      const strip = await screen.findByRole('alert');
      expect(strip).toHaveTextContent('login.py is no longer in conflict.');
      expect(within(strip).getByRole('button', { name: 'Copy my text' })).toBeInTheDocument();
      expect(editor().value).toBe('half done\n');
    });
  });

  it('closes a clean resolver without a question', async () => {
    await openResolver();

    fireEvent.keyDown(layout(), { key: 'Escape' });

    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it.each([
    ['a clean editor', null],
    ['unsaved edits', 'half done\n'],
  ])('keeps Escape for the open ⋯ menu over %s, then gives it back', async (_name, edit) => {
    await openResolver();
    if (edit !== null) type(edit);
    const trigger = screen.getByRole('button', { name: 'More conflict actions' });
    fireEvent.click(trigger);

    fireEvent.keyDown(screen.getByRole('menuitem', { name: 'View diff' }), { key: 'Escape' });

    expect(onClose).not.toHaveBeenCalled();
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(queryEditor()).not.toBeNull();
    expect(trigger).toHaveFocus();

    fireEvent.keyDown(trigger, { key: 'Escape' });

    if (edit === null) expect(onClose).toHaveBeenCalledTimes(1);
    else expect(screen.getByRole('alertdialog', { name: 'Discard your edits?' })).toBeInTheDocument();
  });

  it('closes a clean resolver, with a notice, when the file is resolved elsewhere', async () => {
    await openResolver();

    await pollWith(stopped('e2', [entry()], ['src/login.py']));

    await waitFor(() => {
      expect(queryEditor()).toBeNull();
    });
    expect(notice()).toHaveTextContent('login.py is no longer in conflict.');
    expect(layout()).toHaveAttribute('data-view', 'list');
  });

  describe('Resolved section', () => {
    const restoreButton = (): HTMLElement =>
      screen.getByRole('button', { name: 'Restore the conflict in src/session.py' });

    it('lists the restorable paths right under Conflicts, outside the selection', async () => {
      await renderStopped(
        stopped('e1', [conflictEntry('src/login.py'), entry()], ['src/session.py']),
      );

      const titles = [...document.querySelectorAll('.git-changes-section h3 > span:first-child')].map(
        (node) => node.textContent,
      );
      expect(titles).toEqual(['Conflicts', 'Resolved', 'Changes']);
      expect(sectionOf(restoreButton())).toHaveTextContent('session.py');
      expect(screen.queryByRole('checkbox', { name: 'Select src/session.py' })).toBeNull();
    });

    it('stands alone, instead of "Working tree clean", when the resolutions equal HEAD', async () => {
      await renderStopped(stopped('e1', [], ['src/session.py']));

      expect(restoreButton()).toBeInTheDocument();
      expect(screen.queryByText('Working tree clean')).toBeNull();
    });

    it('is absent on an agent without the resolver, whatever the status carries', async () => {
      await renderStopped(stopped('e1', [entry()], ['src/session.py']), {
        ...ALL_GIT,
        rebase: true,
        resolve: true,
      });

      expect(screen.queryByText('Resolved')).toBeNull();
      expect(screen.queryByRole('button', { name: /Restore the conflict/ })).toBeNull();
    });

    it('asks first, with Cancel focused, then rebuilds the conflict and reports it', async () => {
      await renderStopped(stopped('e1', [entry()], ['src/session.py']));

      fireEvent.click(restoreButton());

      const confirm = screen.getByRole('alertdialog', { name: 'Restore the conflict?' });
      expect(confirm).toHaveTextContent(
        'session.py goes back to its conflicted state. The resolution you applied is discarded.',
      );
      expect(within(confirm).getByRole('button', { name: 'Cancel' })).toHaveFocus();
      expect(api.conflictCalls).toEqual([]);

      api.status = stopped('e2', [conflictEntry('src/session.py'), entry()]);
      fireEvent.click(within(confirm).getByRole('button', { name: 'Restore conflict' }));

      await waitFor(() => {
        expect(notice()).toHaveTextContent('Restored the conflict in src/session.py.');
      });
      expect(api.conflictCalls).toEqual([
        { kind: 'restore', request: { path: 'src/session.py', style: 'merge' } },
      ]);
      expect(screen.queryByRole('alertdialog')).toBeNull();
      expect(await screen.findByRole('button', { name: /session\.py/, pressed: false })).toBeInTheDocument();
    });

    it('cancels without a request', async () => {
      await renderStopped(stopped('e1', [entry()], ['src/session.py']));
      fireEvent.click(restoreButton());

      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

      expect(screen.queryByRole('alertdialog')).toBeNull();
      expect(api.conflictCalls).toEqual([]);
    });

    it('reports a conflict git can no longer rebuild', async () => {
      await renderStopped(stopped('e1', [entry()], ['src/session.py']));
      api.refuseConflict = failure(409, { code: 'not_restorable', message: 'src/session.py' });
      fireEvent.click(restoreButton());

      fireEvent.click(screen.getByRole('button', { name: 'Restore conflict' }));

      await waitFor(() => {
        expect(notice()).toHaveTextContent('The conflict in src/session.py can no longer be rebuilt.');
      });
      expect(notice()).toHaveAttribute('data-tone', 'danger');
    });
  });
});

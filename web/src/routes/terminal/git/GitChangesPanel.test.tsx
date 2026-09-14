import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  SessionGitDiff,
  SessionGitDiffRequest,
  SessionGitEntry,
  SessionGitStatus,
} from '../../../api/sessionGitRepository';

// Both network functions shadowed, as in `useSessionGit.test.tsx`: the status
// the next poll answers with is whatever `api.status` holds, and every diff
// request is recorded, because the scope it asks for is what a selection that
// followed its file across sections must get right. A diff answers with the
// request echoed plus `api.shape` (which also sees the session asked), and
// while `api.hold` is set it waits in `api.release` so a case can look at the
// pane mid-load.
const api = vi.hoisted(() => ({
  status: undefined as SessionGitStatus | undefined,
  diffs: [] as SessionGitDiffRequest[],
  shape: undefined as
    | ((request: SessionGitDiffRequest, sessionId: string) => Partial<SessionGitDiff>)
    | undefined,
  hold: false,
  release: [] as (() => void)[],
}));

vi.mock('../../../api/sessionGitRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/sessionGitRepository')>();
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
  };
});

const { queryKeys } = await import('../../../lib/queryKeys');
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

let client: QueryClient;
const onClose = vi.fn<() => void>();

const panel = (sessionId = 's1') => (
  <QueryClientProvider client={client}>
    <GitChangesPanel agentId="a1" sessionId={sessionId} onClose={onClose} />
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

const contextGroup = (): HTMLElement | null =>
  screen.queryByRole('group', { name: 'Context lines' });
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
});

afterEach(() => {
  cleanup();
  client.clear();
  onClose.mockClear();
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

describe('GitChangesPanel diff Context control', () => {
  it('waits for the diff before offering Context lines for a modified file', async () => {
    // Whether the choice can matter is read off the response, so there is no
    // control to show while the first answer is still out.
    api.shape = withHunk('modified');
    api.hold = true;
    await selectApp();

    expect(screen.getByText('Loading diff…')).toBeInTheDocument();
    expect(contextGroup()).toBeNull();

    await releaseDiffs();

    const group = await screen.findByRole('group', { name: 'Context lines' });
    expect(within(group).getByRole('button', { name: '3' })).toHaveAttribute('aria-pressed', 'true');
    expect(diffBody()).toHaveAttribute('aria-busy', 'false');
  });

  it('offers no Context lines for an added file, which has no unchanged lines to widen', async () => {
    api.shape = withHunk('added');
    await selectApp();

    await screen.findByText('src/app.py at 3');
    expect(contextGroup()).toBeNull();
  });

  it('keeps the previous diff and its control on screen, marked busy, while a new context loads', async () => {
    api.shape = withHunk('modified');
    await selectApp();
    await screen.findByText('src/app.py at 3');

    const choices = await screen.findByRole('group', { name: 'Context lines' });
    api.hold = true;
    fireEvent.click(within(choices).getByRole('button', { name: '10' }));
    await waitFor(() => {
      expect(api.diffs.at(-1)).toMatchObject({ path: 'src/app.py', context: 10 });
    });

    expect(screen.queryByText('Loading diff…')).toBeNull();
    expect(screen.getByText('src/app.py at 3')).toBeInTheDocument();
    expect(diffBody()).toHaveAttribute('aria-busy', 'true');
    expect(metaSpinner()).not.toBeNull();
    const group = contextGroup();
    if (group === null) throw new Error('Context control dropped while the new diff loads');
    expect(within(group).getByRole('button', { name: '10' })).toHaveAttribute('aria-pressed', 'true');

    await releaseDiffs();

    await screen.findByText('src/app.py at 10');
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

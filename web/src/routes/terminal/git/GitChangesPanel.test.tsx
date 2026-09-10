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
// followed its file across sections must get right.
const api = vi.hoisted(() => ({
  status: undefined as SessionGitStatus | undefined,
  diffs: [] as SessionGitDiffRequest[],
}));

vi.mock('../../../api/sessionGitRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/sessionGitRepository')>();
  return {
    ...actual,
    getSessionGitStatus: () =>
      api.status === undefined
        ? Promise.reject(new Error('no status body staged'))
        : Promise.resolve(api.status),
    getSessionGitDiff: (_agentId: string, _sessionId: string, request: SessionGitDiffRequest) => {
      api.diffs.push(request);
      return Promise.resolve(diff(request));
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

function renderPanel() {
  return render(
    <QueryClientProvider client={client}>
      <GitChangesPanel agentId="a1" sessionId="s1" onClose={onClose} />
    </QueryClientProvider>,
  );
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

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  api.status = undefined;
  api.diffs.length = 0;
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

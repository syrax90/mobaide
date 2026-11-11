import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubLocalStorage } from '../../test/localStorageStub';
import { queryKeys } from '../../lib/queryKeys';
import { UNDO_WINDOW_MS } from '../../lib/useDeferredCommit';
import { projectInfoSchema, type ProjectInfo } from '../../protocol/dto/projects';

// No agent token means no URL, so no socket is ever constructed — the seam
// `SessionsScreen.test.tsx` uses. What is under test is the reorder contract,
// not the realtime layer.
vi.mock('../../auth/agentTokens', () => ({
  ensureAgentToken: () => Promise.resolve(null),
  clearAgentToken: () => undefined,
}));

const project = (name: string, order: number): ProjectInfo =>
  projectInfoSchema.parse({ name, path: `/srv/${name}`, order });

const api = vi.hoisted(() => ({
  projects: [] as { name: string; order: number }[],
  reorders: [] as string[][],
  deletes: [] as string[],
  /** While set, a DELETE hangs until the test calls `settleDelete`. */
  holdDeletes: false,
  settleDelete: null as (() => void) | null,
}));

vi.mock('../../api/projectRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/projectRepository')>();
  return {
    ...actual,
    listProjects: () => Promise.resolve(api.projects.map((row) => project(row.name, row.order))),
    // The 200 body is the re-sorted list, which the screen writes straight into
    // the cache — so the fake has to answer with it.
    reorderProjects: (_agentId: string, names: readonly string[]) => {
      api.reorders.push([...names]);
      return Promise.resolve(names.map((name, index) => project(name, index)));
    },
    deleteProject: (_agentId: string, name: string) => {
      api.deletes.push(name);
      if (!api.holdDeletes) return Promise.resolve();
      return new Promise<void>((resolve) => {
        api.settleDelete = resolve;
      });
    },
  };
});

// Before any persisted store is imported.
stubLocalStorage();

const { stopAllRealtime } = await import('../../realtime');
const { useToastStore } = await import('../../stores/toastStore');
const { ProjectList } = await import('./ProjectList');

/** Row order, read from the move buttons' accessible names. */
const rowNames = (): string[] =>
  screen
    .getAllByRole('button', { name: /^Move .+ up$/ })
    .map((button) => (button.getAttribute('aria-label') ?? '').slice(5, -3));

const row = (name: string): HTMLElement => {
  const li = screen.getByRole('button', { name: `Move ${name} up` }).closest('li');
  if (li === null) throw new Error(`no row for ${name}`);
  return li;
};

let client: QueryClient;

function renderList() {
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/projects']}>
        <ProjectList agentId="agent-1" online />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(async () => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  api.projects = [
    { name: 'alpha', order: 0 },
    { name: 'beta', order: 1 },
    { name: 'gamma', order: 2 },
  ];
  api.reorders.length = 0;
  api.deletes.length = 0;
  api.holdDeletes = false;
  api.settleDelete = null;
  useToastStore.setState({ toasts: [] });
  renderList();
  await waitFor(() => {
    expect(rowNames()).toEqual(['alpha', 'beta', 'gamma']);
  });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  stopAllRealtime();
  client.clear();
  localStorage.clear();
});

describe('ProjectList reorder', () => {
  it('sends the whole name list in its new order and shows the move at once', async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Move alpha down' }));

    expect(rowNames()).toEqual(['beta', 'alpha', 'gamma']);
    await waitFor(() => {
      expect(api.reorders).toEqual([['beta', 'alpha', 'gamma']]);
    });
    expect(rowNames()).toEqual(['beta', 'alpha', 'gamma']);
  });

  it('still names a row hidden by an open undo window in the payload', async () => {
    // The server 400s on any missing name, so a deferred delete may not shrink
    // the request while its window is open.
    fireEvent.click(within(row('beta')).getByRole('button', { name: 'Delete' }));
    expect(rowNames()).toEqual(['alpha', 'gamma']);

    fireEvent.click(screen.getByRole('button', { name: 'Move alpha down' }));

    await waitFor(() => {
      expect(api.reorders).toEqual([['beta', 'gamma', 'alpha']]);
    });
    expect(api.deletes).toEqual([]);
  });
});

describe('ProjectList editor against a vanishing target', () => {
  it('closes the open editor when its target leaves the list', async () => {
    // The card itself is the edit affordance; its accessible name is the
    // project's name followed by its path.
    fireEvent.click(within(row('beta')).getByRole('button', { name: /^beta/ }));
    expect(screen.getByRole('dialog', { name: 'Edit project' })).toBeInTheDocument();

    // What `projects.changed` does when another client deletes or renames the
    // project: `projectsCache` writes the whole authoritative list into the cache.
    client.setQueryData(queryKeys.projects('agent-1'), [project('alpha', 0), project('gamma', 2)]);

    // Left open, the dialog would quietly turn into a *create* form — `editing`
    // resolves by name, so the same title and the same dirty fields would now
    // Save through `createProject`, Linear key included.
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
    expect(useToastStore.getState().toasts.map((toast) => toast.message)).toEqual([
      'That project no longer exists.',
    ]);
  });
});

describe('ProjectList deferred delete', () => {
  it('keeps the row hidden while the committed DELETE is still in flight', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.holdDeletes = true;

    fireEvent.click(within(row('beta')).getByRole('button', { name: 'Delete' }));
    expect(rowNames()).toEqual(['alpha', 'gamma']);
    expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(UNDO_WINDOW_MS);
    });

    // `flush` clears its payload *before* invoking `onCommit` — the gone undo
    // bar is that emptied payload — so from here the hide rests entirely on the
    // in-flight mutation. A row painting again for the length of the request is
    // long enough for the user to press Delete a second time and get a spurious
    // "not found".
    expect(api.deletes).toEqual(['beta']);
    expect(screen.queryByRole('button', { name: 'Undo' })).not.toBeInTheDocument();
    expect(rowNames()).toEqual(['alpha', 'gamma']);

    // The agent answers. The row must not flash back as the mutation settles
    // and the invalidation's refetch lands, and nothing may report a failure.
    api.projects = [
      { name: 'alpha', order: 0 },
      { name: 'gamma', order: 2 },
    ];
    vi.useRealTimers();
    const settle = api.settleDelete;
    if (settle === null) throw new Error('the DELETE was never sent');
    settle();

    await waitFor(() => {
      expect(rowNames()).toEqual(['alpha', 'gamma']);
    });
    expect(useToastStore.getState().toasts).toEqual([]);
  });
});

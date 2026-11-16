import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubLocalStorage } from '../../test/localStorageStub';
import { queryKeys } from '../../lib/queryKeys';
import { UNDO_WINDOW_MS } from '../../lib/useDeferredCommit';
import { workspaceInfoSchema, type WorkspaceInfo } from '../../protocol/dto/workspaces';

// No agent token means no URL, so no socket is ever constructed — the seam
// `SessionsScreen.test.tsx` uses. What is under test is the reorder contract,
// not the realtime layer.
vi.mock('../../auth/agentTokens', () => ({
  ensureAgentToken: () => Promise.resolve(null),
  clearAgentToken: () => undefined,
}));

const workspace = (name: string, order: number): WorkspaceInfo =>
  workspaceInfoSchema.parse({ name, path: `/srv/${name}`, order });

const api = vi.hoisted(() => ({
  workspaces: [] as { name: string; order: number }[],
  reorders: [] as string[][],
  deletes: [] as string[],
  /** While set, a DELETE hangs until the test calls `settleDelete`. */
  holdDeletes: false,
  settleDelete: null as (() => void) | null,
}));

vi.mock('../../api/workspaceRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/workspaceRepository')>();
  return {
    ...actual,
    listWorkspaces: () => Promise.resolve(api.workspaces.map((row) => workspace(row.name, row.order))),
    // The 200 body is the re-sorted list, which the screen writes straight into
    // the cache — so the fake has to answer with it.
    reorderWorkspaces: (_agentId: string, names: readonly string[]) => {
      api.reorders.push([...names]);
      return Promise.resolve(names.map((name, index) => workspace(name, index)));
    },
    deleteWorkspace: (_agentId: string, name: string) => {
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
const { WorkspaceList } = await import('./WorkspaceList');

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
      <MemoryRouter initialEntries={['/workspaces']}>
        <WorkspaceList agentId="agent-1" online />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(async () => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  api.workspaces = [
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

describe('WorkspaceList reorder', () => {
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

describe('WorkspaceList editor against a vanishing target', () => {
  it('closes the open editor when its target leaves the list', async () => {
    // The card itself is the edit affordance; its accessible name is the
    // workspace's name followed by its path.
    fireEvent.click(within(row('beta')).getByRole('button', { name: /^beta/ }));
    expect(screen.getByRole('dialog', { name: 'Edit workspace' })).toBeInTheDocument();

    // What `projects.changed` does when another client deletes or renames the
    // workspace: `workspacesCache` writes the whole authoritative list into the cache.
    client.setQueryData(queryKeys.workspaces('agent-1'), [workspace('alpha', 0), workspace('gamma', 2)]);

    // Left open, the dialog would quietly turn into a *create* form — `editing`
    // resolves by name, so the same title and the same dirty fields would now
    // Save through `createWorkspace`, Linear key included.
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
    expect(useToastStore.getState().toasts.map((toast) => toast.message)).toEqual([
      'That workspace no longer exists.',
    ]);
  });
});

describe('WorkspaceList deferred delete', () => {
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
    api.workspaces = [
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

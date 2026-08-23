import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GitDeleteItem, GitDeleteResult, GitItem } from '../../../api/gitRepository';
import type { BusyHolder } from '../../../protocol/dto/gitConflict';

// No agent token means no URL, so no socket is ever constructed.
vi.mock('../../../auth/agentTokens', () => ({
  ensureAgentToken: () => Promise.resolve(null),
  clearAgentToken: () => undefined,
}));

type Outcome =
  | { kind: 'results'; results: GitDeleteResult[] }
  | { kind: 'busy'; holders: BusyHolder[] | null };

const api = vi.hoisted(() => ({
  items: [] as GitItem[],
  calls: [] as GitDeleteItem[][],
  outcomes: [] as Outcome[],
}));

vi.mock('../../../api/gitRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/gitRepository')>();
  return {
    ...actual,
    listGitItems: () => Promise.resolve(api.items.map((item) => ({ ...item }))),
    deleteGitItems: (_agentId: string, _project: string, items: readonly GitDeleteItem[]) => {
      api.calls.push(items.map((item) => ({ ...item })));
      const outcome = api.outcomes.shift();
      if (outcome?.kind === 'busy') {
        // The real class, so the screen's `instanceof` branch is the one taken.
        return Promise.reject(new actual.GitBusyError(outcome.holders));
      }
      return Promise.resolve(
        outcome?.results ??
          items.map((item) => ({
            kind: item.kind,
            target: item.branch ?? item.worktree_path ?? '',
            ok: true,
          })),
      );
    },
  };
});

const { GitScreen } = await import('./GitScreen');

const branch = (name: string): GitItem => ({
  kind: 'branch_only',
  branch: name,
  worktree_path: null,
  ahead: 0,
  behind: 0,
  detached: false,
  gone: false,
  is_current: false,
  locked: false,
  locked_reason: null,
  prunable: false,
  upstream: null,
});

const holder = (over: Partial<BusyHolder> & { kind: string }): BusyHolder => ({
  session_id: undefined,
  project_name: undefined,
  cwd: undefined,
  worktree_path: undefined,
  ...over,
});

let client: QueryClient;

const renderScreen = () =>
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/agents/agent-1/projects/proj/git']}>
        <Routes>
          <Route path="/agents/:agentId/projects/:projectName/git" element={<GitScreen />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );

/** Queue a row and end its undo window early, which is what sends the batch. */
const deleteNow = async (name: string) => {
  fireEvent.click(screen.getByRole('button', { name: `Delete ${name}` }));
  fireEvent.click(await screen.findByRole('button', { name: 'Dismiss' }));
};

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  api.items = [branch('feature-a'), branch('feature-b')];
  api.calls.length = 0;
  api.outcomes.length = 0;
});

afterEach(() => {
  cleanup();
  client.clear();
});

describe('GitScreen busy conflict', () => {
  it('restores every hidden row and names the holders', async () => {
    api.outcomes = [
      {
        kind: 'busy',
        holders: [
          holder({ kind: 'session', session_id: 's1', cwd: '/wt/x' }),
          holder({ kind: 'auto_scan', project_name: 'proj', worktree_path: '/wt/y' }),
        ],
      },
    ];
    renderScreen();
    await screen.findByRole('button', { name: 'Delete feature-a' });

    await deleteNow('feature-a');

    const dialog = await screen.findByRole('alertdialog');
    expect(within(dialog).getByText('Item is in use')).toBeInTheDocument();
    expect(within(dialog).getByText('Session: /wt/x')).toBeInTheDocument();
    expect(within(dialog).getByText('Auto-scan proj: /wt/y')).toBeInTheDocument();
    // The guard runs before any deletion, so the row is back.
    expect(screen.getByRole('button', { name: 'Delete feature-a' })).toBeInTheDocument();
  });

  it('reports a plain message when the 409 body is not a busy conflict', async () => {
    api.outcomes = [{ kind: 'busy', holders: null }];
    renderScreen();
    await screen.findByRole('button', { name: 'Delete feature-a' });

    await deleteNow('feature-a');

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Delete feature-a' })).toBeInTheDocument();
    });
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });
});

describe('GitScreen force delete', () => {
  it('brings the row back to prompt, then re-sends it with force set', async () => {
    api.outcomes = [
      {
        kind: 'results',
        results: [
          { kind: 'branch_only', target: 'feature-a', ok: false, error: 'force_required' },
        ],
      },
      { kind: 'results', results: [{ kind: 'branch_only', target: 'feature-a', ok: true }] },
    ];
    renderScreen();
    await screen.findByRole('button', { name: 'Delete feature-a' });

    await deleteNow('feature-a');

    const prompt = await screen.findByRole('alertdialog');
    expect(within(prompt).getByText('Force delete?')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Delete feature-a' })).toBeInTheDocument();

    fireEvent.click(within(prompt).getByRole('button', { name: 'Force delete' }));

    await waitFor(() => {
      expect(api.calls).toHaveLength(2);
    });
    expect(api.calls.at(0)?.at(0)?.force).toBe(false);
    expect(api.calls.at(1)?.at(0)).toEqual({
      kind: 'branch_only',
      branch: 'feature-a',
      worktree_path: null,
      force: true,
    });
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Delete feature-a' })).toBeNull();
    });
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('leaves the row visible and sends nothing more when the prompt is cancelled', async () => {
    api.outcomes = [
      {
        kind: 'results',
        results: [
          { kind: 'branch_only', target: 'feature-a', ok: false, error: 'force_required' },
        ],
      },
    ];
    renderScreen();
    await screen.findByRole('button', { name: 'Delete feature-a' });

    await deleteNow('feature-a');

    const prompt = await screen.findByRole('alertdialog');
    fireEvent.click(within(prompt).getByRole('button', { name: 'Cancel' }));

    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(screen.getByRole('button', { name: 'Delete feature-a' })).toBeInTheDocument();
    expect(api.calls).toHaveLength(1);
  });
});

describe('GitScreen undo', () => {
  it('un-hides the row and sends nothing when the window is cancelled', async () => {
    renderScreen();
    await screen.findByRole('button', { name: 'Delete feature-a' });

    fireEvent.click(screen.getByRole('button', { name: 'Delete feature-a' }));
    expect(screen.queryByRole('button', { name: 'Delete feature-a' })).toBeNull();

    fireEvent.click(await screen.findByRole('button', { name: 'Undo' }));

    expect(screen.getByRole('button', { name: 'Delete feature-a' })).toBeInTheDocument();
    expect(api.calls).toEqual([]);
  });
});

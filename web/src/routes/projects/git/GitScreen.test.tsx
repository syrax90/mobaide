import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  GitBranchCreateRequest,
  GitDeleteItem,
  GitDeleteResult,
  GitItem,
} from '../../../api/gitRepository';
import type { AgentVersionInfo } from '../../../protocol/dto/agentVersion';
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
  version: undefined as AgentVersionInfo | undefined,
  /** The refs `checkoutGitRef` was asked for; an `Error` outcome rejects the next one. */
  checkouts: [] as string[],
  /** The `force` flag of each `checkouts` entry. */
  checkoutForces: [] as boolean[],
  checkoutOutcome: undefined as Error | undefined,
  creates: [] as GitBranchCreateRequest[],
  createOutcome: undefined as Error | undefined,
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
    checkoutGitRef: (_agentId: string, _project: string, ref: string, force = false) => {
      api.checkouts.push(ref);
      api.checkoutForces.push(force);
      if (api.checkoutOutcome !== undefined) return Promise.reject(api.checkoutOutcome);
      const remote = ref.startsWith('origin/');
      return Promise.resolve({
        branch: remote ? ref.slice('origin/'.length) : ref,
        checked_out: true,
        created: remote,
      });
    },
    createGitBranch: (_agentId: string, _project: string, request: GitBranchCreateRequest) => {
      api.creates.push({ ...request });
      if (api.createOutcome !== undefined) return Promise.reject(api.createOutcome);
      return Promise.resolve({
        branch: request.name,
        checked_out: request.checkout === true,
        created: true,
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

const { GitScreen } = await import('./GitScreen');
const { GitBusyError } = await import('../../../api/gitRepository');
const { toApiError } = await import('../../../api/errors');
const { toSessionGitError } = await import('../../../api/sessionGitRepository');
const { agentVersionSchema } = await import('../../../protocol/dto/agentVersion');
const { useToastStore } = await import('../../../stores/toastStore');

const branch = (name: string, over: Partial<GitItem> = {}): GitItem => ({
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
  remote: null,
  subject: null,
  committed_at: null,
  ...over,
});

const remoteBranch = (name: string): GitItem =>
  branch(`origin/${name}`, { kind: 'remote_branch', remote: 'origin' });

const holder = (over: Partial<BusyHolder> & { kind: string }): BusyHolder => ({
  session_id: undefined,
  project_name: undefined,
  cwd: undefined,
  worktree_path: undefined,
  ...over,
});

const agentVersion = (git: Record<string, unknown>): AgentVersionInfo =>
  agentVersionSchema.parse({
    name: 'mobaide-agent',
    server_version: '1.4.0',
    protocol_versions: [7],
    min_client_version: 1,
    capabilities: { git },
  });

/** A failure as the repository throws it off the wire. */
const wireFailure = (httpStatus: number, detail: unknown): Error => {
  const failure = toSessionGitError(
    toApiError(new Response(null, { status: httpStatus }), { detail }),
  );
  if (!(failure instanceof Error)) throw new Error('the repository only throws Error subclasses');
  return failure;
};

const lastToast = () => useToastStore.getState().toasts.at(-1)?.message;

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
  api.version = agentVersion({ branches: true });
  api.checkouts.length = 0;
  api.checkoutForces.length = 0;
  api.checkoutOutcome = undefined;
  api.creates.length = 0;
  api.createOutcome = undefined;
  useToastStore.setState({ toasts: [] });
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

describe('GitScreen branches capability', () => {
  it('keeps the delete-only screen until the agent advertises branches', async () => {
    api.version = agentVersion({ changes: true });
    api.items = [branch('main', { is_current: true }), branch('feature-a')];
    renderScreen();
    await screen.findByRole('button', { name: 'Delete feature-a' });

    expect(screen.queryByRole('button', { name: 'New branch…' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Check out feature-a' })).toBeNull();
  });

  it('offers New branch and Check out on the rows nothing has checked out', async () => {
    api.items = [
      branch('main', { is_current: true }),
      branch('feature-a'),
      branch('topic', { kind: 'branch_with_worktree', worktree_path: '/wt/topic' }),
      remoteBranch('feature-x'),
    ];
    renderScreen();

    expect(await screen.findByRole('button', { name: 'New branch…' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Check out feature-a' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Check out origin/feature-x' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Check out main' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Check out topic' })).toBeNull();
  });
});

describe('GitScreen remote branches', () => {
  it('lists them in a collapsed section with a remote chip and no delete', async () => {
    api.items = [branch('feature-a'), remoteBranch('feature-x'), remoteBranch('main')];
    renderScreen();
    await screen.findByRole('button', { name: 'Delete feature-a' });

    const summary = screen.getByText('Remote branches (2)');
    const section = summary.closest('details');
    expect(section).not.toBeNull();
    expect(section?.open).toBe(false);
    // The remote items share one remote, so they file under an "origin"
    // folder (open by default) with just their tail as the visible label —
    // the full name survives in the title and the accessible names below.
    expect(
      within(section as HTMLElement).getByText('origin', { selector: '.git-folder-name' }),
    ).toBeInTheDocument();
    expect(within(section as HTMLElement).getByText('feature-x')).toBeInTheDocument();
    expect(within(section as HTMLElement).getAllByText('origin')).toHaveLength(3);
    expect(screen.queryByRole('button', { name: 'Delete origin/feature-x' })).toBeNull();
    expect(screen.queryByRole('checkbox', { name: 'Select origin/feature-x' })).toBeNull();
  });

  it('shows the filter from eight rows on, and a filter opens the remote section', async () => {
    api.items = [
      ...['a', 'b', 'c', 'd', 'e', 'f'].map((suffix) => branch(`feature-${suffix}`)),
      remoteBranch('feature-x'),
      remoteBranch('main'),
    ];
    renderScreen();
    const filter = await screen.findByRole('searchbox', { name: 'Filter branches' });
    const section = screen.getByText('Remote branches (2)').closest('details');
    expect(section?.open).toBe(false);

    fireEvent.change(filter, { target: { value: 'feature-x' } });

    expect(screen.getByText('Remote branches (1)').closest('details')?.open).toBe(true);
    expect(screen.queryByRole('button', { name: 'Delete feature-a' })).toBeNull();
    expect(screen.queryByText('No matching branches or worktrees.')).toBeNull();
    expect(screen.getByRole('button', { name: 'Check out origin/feature-x' })).toBeInTheDocument();
  });

});

describe('GitScreen filter', () => {
  const eightRows = () => {
    api.items = [
      ...['a', 'b', 'c', 'd', 'e', 'f'].map((suffix) => branch(`feature-${suffix}`)),
      remoteBranch('feature-x'),
      remoteBranch('main'),
    ];
  };

  it('shows the notice only when neither list has a match', async () => {
    eightRows();
    renderScreen();
    const filter = await screen.findByRole('searchbox', { name: 'Filter branches' });

    fireEvent.change(filter, { target: { value: 'nothing-like-this' } });

    expect(screen.getByText('No matching branches or worktrees.')).toBeInTheDocument();
    expect(screen.queryByText(/Remote branches/)).toBeNull();
    expect(screen.queryByRole('list')).toBeNull();
  });
});

describe('GitScreen check out', () => {
  const rowsWithRemote = () => {
    api.items = [branch('main', { is_current: true }), branch('feature-a'), remoteBranch('feature-x')];
  };

  it("confirms, then sends the row's ref and reports what the agent checked out", async () => {
    rowsWithRemote();
    renderScreen();
    fireEvent.click(await screen.findByRole('button', { name: 'Check out origin/feature-x' }));

    const prompt = await screen.findByRole('alertdialog');
    expect(within(prompt).getByText('Check out origin/feature-x?')).toBeInTheDocument();
    expect(
      within(prompt).getByText(
        'Check out origin/feature-x in the project folder of proj? ' +
          'Sessions started in that folder afterwards use this branch.',
      ),
    ).toBeInTheDocument();
    expect(api.checkouts).toEqual([]);

    fireEvent.click(within(prompt).getByRole('button', { name: 'Check out' }));

    await waitFor(() => {
      expect(api.checkouts).toEqual(['origin/feature-x']);
    });
    expect(screen.queryByRole('alertdialog')).toBeNull();
    await waitFor(() => {
      expect(lastToast()).toBe('Checked out feature-x');
    });
  });

  it('sends nothing when the confirmation is cancelled', async () => {
    rowsWithRemote();
    renderScreen();
    fireEvent.click(await screen.findByRole('button', { name: 'Check out feature-a' }));

    const prompt = await screen.findByRole('alertdialog');
    fireEvent.click(within(prompt).getByRole('button', { name: 'Cancel' }));

    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(api.checkouts).toEqual([]);
  });

  const refuseAsBusy = async () => {
    rowsWithRemote();
    api.checkoutOutcome = new GitBusyError([holder({ kind: 'session', session_id: 's1', cwd: '/repo' })]);
    renderScreen();
    fireEvent.click(await screen.findByRole('button', { name: 'Check out feature-a' }));
    fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Check out' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Project folder in use' });
    return dialog;
  };

  it('names the holders on a busy conflict and offers to check out anyway', async () => {
    const dialog = await refuseAsBusy();

    expect(within(dialog).getByText('Session: /repo')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Check out anyway' })).toBeInTheDocument();
    expect(api.checkouts).toEqual(['feature-a']);
    expect(api.checkoutForces).toEqual([false]);
  });

  it('retries with force once the user confirms the busy prompt', async () => {
    const dialog = await refuseAsBusy();
    api.checkoutOutcome = undefined;

    fireEvent.click(within(dialog).getByRole('button', { name: 'Check out anyway' }));

    await waitFor(() => {
      expect(lastToast()).toBe('Checked out feature-a');
    });
    expect(api.checkouts).toEqual(['feature-a', 'feature-a']);
    expect(api.checkoutForces).toEqual([false, true]);
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('asks to update an agent that refuses a forced checkout', async () => {
    const dialog = await refuseAsBusy();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Check out anyway' }));

    await waitFor(() => {
      expect(lastToast()).toBe(
        'The project folder is still in use. Update the agent to check out anyway.',
      );
    });
    expect(api.checkoutForces).toEqual([false, true]);
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

});

describe('GitScreen new branch', () => {
  const openDialog = async () => {
    api.items = [branch('main', { is_current: true }), branch('feature-a'), remoteBranch('feature-x')];
    renderScreen();
    fireEvent.click(await screen.findByRole('button', { name: 'New branch…' }));
    return screen.getByRole('dialog');
  };

  it('starts from the checked-out branch and sends the typed form', async () => {
    const dialog = await openDialog();
    const startPoint = within(dialog).getByLabelText('Start point');
    expect(startPoint).toHaveValue('main');
    expect(within(dialog).getByRole('button', { name: 'Create' })).toBeDisabled();

    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: '  feature/new ' } });
    fireEvent.click(within(dialog).getByLabelText(/Check out in the project folder of proj/));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));

    await waitFor(() => {
      expect(api.creates).toEqual([
        { name: 'feature/new', start_point: 'main', checkout: true, force: false },
      ]);
    });
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).toBeNull();
    });
    expect(lastToast()).toBe('Created and checked out feature/new');
  });

  it('omits the start point for HEAD and keeps the dialog open with the refusal inline', async () => {
    api.createOutcome = wireFailure(409, { code: 'exists', message: 'feature/new' });
    const dialog = await openDialog();

    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'feature/new' } });
    fireEvent.change(within(dialog).getByLabelText('Start point'), { target: { value: '' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));

    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'A branch named feature/new already exists.',
    );
    expect(api.creates).toEqual([{ name: 'feature/new', checkout: false, force: false }]);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(lastToast()).toBeUndefined();
  });

  it('closes on a busy conflict and hands the holders to the screen dialog', async () => {
    api.createOutcome = new GitBusyError([holder({ kind: 'session', session_id: 's1', cwd: '/repo' })]);
    const dialog = await openDialog();

    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'feature/new' } });
    fireEvent.click(within(dialog).getByLabelText(/Check out in the project folder of proj/));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));

    const busy = await screen.findByRole('alertdialog', { name: 'Project folder in use' });
    expect(within(busy).getByText('Session: /repo')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(api.creates).toEqual([
      { name: 'feature/new', start_point: 'main', checkout: true, force: false },
    ]);
    expect(lastToast()).toBeUndefined();

    api.createOutcome = undefined;
    fireEvent.click(within(busy).getByRole('button', { name: 'Check out anyway' }));

    await waitFor(() => {
      expect(lastToast()).toBe('Created and checked out feature/new');
    });
    expect(api.creates).toEqual([
      { name: 'feature/new', start_point: 'main', checkout: true, force: false },
      { name: 'feature/new', start_point: 'main', checkout: true, force: true },
    ]);
  });

  it('closes with a plain toast when the busy 409 names no holders', async () => {
    api.createOutcome = new GitBusyError(null);
    const dialog = await openDialog();

    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'feature/new' } });
    fireEvent.click(within(dialog).getByLabelText(/Check out in the project folder of proj/));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).toBeNull();
    });
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(lastToast()).toBe('The project folder is in use.');
  });

  it('skips an orphan worktree even when it still names its deleted branch', async () => {
    api.items = [
      branch('main', { is_current: true }),
      branch('gone', { kind: 'orphan_worktree', worktree_path: '/wt/gone' }),
      branch('feature-a'),
      remoteBranch('feature-x'),
    ];
    renderScreen();
    fireEvent.click(await screen.findByRole('button', { name: 'New branch…' }));
    const dialog = screen.getByRole('dialog');

    const options = within(dialog)
      .getAllByRole('option')
      .map((option) => option.textContent);
    expect(options).toEqual([
      'HEAD (current commit)',
      'feature-a',
      'main',
      'origin/feature-x (remote)',
    ]);
  });
});

describe('GitScreen new branch start points', () => {
  it('sorts the local and the remote rows each A→Z, ignoring case', async () => {
    api.items = [
      remoteBranch('Zed'),
      branch('main', { is_current: true }),
      branch('Zeta'),
      remoteBranch('apple'),
      branch('alpha'),
    ];
    renderScreen();
    fireEvent.click(await screen.findByRole('button', { name: 'New branch…' }));

    const options = within(screen.getByRole('dialog'))
      .getAllByRole('option')
      .map((option) => option.textContent);
    expect(options).toEqual([
      'HEAD (current commit)',
      'alpha',
      'main',
      'Zeta',
      'origin/apple (remote)',
      'origin/Zed (remote)',
    ]);
  });
});

describe('GitScreen remote delete', () => {
  beforeEach(() => {
    api.version = agentVersion({ branches: true, delete_remote: true });
    api.items = [branch('feature-a'), remoteBranch('feature-x')];
  });

  it('confirms before the undo window, then sends the remote-qualified name', async () => {
    renderScreen();
    fireEvent.click(await screen.findByRole('button', { name: 'Delete origin/feature-x' }));

    const prompt = await screen.findByRole('alertdialog');
    expect(within(prompt).getByText('Delete on the remote?')).toBeInTheDocument();
    expect(
      within(prompt).getByText(
        'Delete origin/feature-x on the remote? ' +
          'This removes it for everyone with access to the remote.',
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Delete origin/feature-x' })).toBeInTheDocument();

    fireEvent.click(within(prompt).getByRole('button', { name: 'Delete' }));

    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Delete origin/feature-x' })).toBeNull();
    fireEvent.click(await screen.findByRole('button', { name: 'Dismiss' }));

    await waitFor(() => {
      expect(api.calls).toEqual([
        [{ kind: 'remote_branch', branch: 'origin/feature-x', worktree_path: null, force: false }],
      ]);
    });
  });

  it('sends nothing and hides nothing when the confirmation is cancelled', async () => {
    renderScreen();
    fireEvent.click(await screen.findByRole('button', { name: 'Delete origin/feature-x' }));

    fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Cancel' }));

    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Delete origin/feature-x' })).toBeInTheDocument();
    expect(api.calls).toEqual([]);
  });

  it('confirms a mixed selection naming only its remote rows, then queues all of it', async () => {
    renderScreen();
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select feature-a' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select origin/feature-x' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete selected' }));

    const prompt = await screen.findByRole('alertdialog');
    expect(
      within(prompt).getByText(
        'Delete origin/feature-x on the remote? ' +
          'This removes it for everyone with access to the remote.',
      ),
    ).toBeInTheDocument();
    fireEvent.click(within(prompt).getByRole('button', { name: 'Delete' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Dismiss' }));

    await waitFor(() => {
      expect(api.calls).toHaveLength(1);
    });
    expect(api.calls[0]?.map((item) => item.branch)).toEqual(['feature-a', 'origin/feature-x']);
  });

  it('keeps the selection when the confirmation is cancelled and clears it once confirmed', async () => {
    renderScreen();
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select feature-a' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select origin/feature-x' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete selected' }));

    fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Cancel' }));

    expect(screen.getByText('2 selected')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Select feature-a' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Select origin/feature-x' })).toBeChecked();
    expect(api.calls).toEqual([]);

    fireEvent.click(screen.getByRole('button', { name: 'Delete selected' }));
    fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Delete' }));

    expect(screen.queryByText('2 selected')).toBeNull();
    expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument();
  });

  it('offers no delete on a remote row when the agent lacks delete_remote', async () => {
    api.version = agentVersion({ branches: true });
    renderScreen();
    await screen.findByRole('button', { name: 'Delete feature-a' });

    expect(screen.queryByRole('button', { name: 'Delete origin/feature-x' })).toBeNull();
    expect(screen.queryByRole('checkbox', { name: 'Select origin/feature-x' })).toBeNull();
  });
});

describe('GitScreen remove worktree', () => {
  beforeEach(() => {
    api.items = [
      branch('main', { is_current: true, kind: 'branch_with_worktree', worktree_path: '/repo' }),
      branch('topic', { kind: 'branch_with_worktree', worktree_path: '/wt/topic' }),
      branch('feature-a'),
    ];
  });

  it('offers it beside a relabelled delete on a linked worktree row only', async () => {
    renderScreen();

    expect(await screen.findByRole('button', { name: 'Remove worktree of topic' })).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Delete branch and worktree topic' }),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Remove worktree of main' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Remove worktree of feature-a' })).toBeNull();
  });

  it('hides the row for the undo window, then sends a worktree-only removal', async () => {
    renderScreen();
    fireEvent.click(await screen.findByRole('button', { name: 'Remove worktree of topic' }));

    expect(screen.queryByRole('button', { name: 'Remove worktree of topic' })).toBeNull();
    expect(screen.queryByRole('alertdialog')).toBeNull();
    fireEvent.click(await screen.findByRole('button', { name: 'Dismiss' }));

    await waitFor(() => {
      expect(api.calls).toEqual([
        [{ kind: 'orphan_worktree', branch: null, worktree_path: '/wt/topic', force: false }],
      ]);
    });
  });
});

describe('GitScreen folders', () => {
  /** A folder toggle's accessible name: its name and visible-row count (the chevron is hidden). */
  const folderName = (name: string, count: number) =>
    new RegExp(`^${name}\\s*\\(${String(count)}\\)$`);

  it('starts a branch folder collapsed and toggles it open and shut', async () => {
    api.items = [branch('feature/a'), branch('feature/b'), branch('main')];
    renderScreen();

    const folder = await screen.findByRole('button', { name: folderName('feature', 2) });
    expect(folder).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('button', { name: 'Delete feature/a' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Delete main' })).toBeInTheDocument();

    fireEvent.click(folder);

    expect(folder).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('button', { name: 'Delete feature/a' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Delete feature/b' })).toBeInTheDocument();

    fireEvent.click(folder);

    expect(folder).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('button', { name: 'Delete feature/a' })).toBeNull();
  });

  it('holds every matching folder open while filtering, with the toggle disabled', async () => {
    api.items = [
      ...['a', 'b', 'c', 'd', 'e', 'f', 'g'].map((suffix) => branch(`feature/${suffix}`)),
      branch('main'),
    ];
    renderScreen();
    const filter = await screen.findByRole('searchbox', { name: 'Filter branches' });

    fireEvent.change(filter, { target: { value: 'feature/c' } });

    const folder = screen.getByRole('button', { name: folderName('feature', 1) });
    expect(folder).toHaveAttribute('aria-expanded', 'true');
    expect(folder).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Delete feature/c' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Delete main' })).toBeNull();

    fireEvent.change(filter, { target: { value: '' } });

    expect(screen.getByRole('button', { name: folderName('feature', 7) })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
  });
});

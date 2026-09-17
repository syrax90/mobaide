import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GitItem } from '../../../api/gitRepository';
import type {
  SessionGitRebaseResult,
  SessionGitRepoInfo,
  SessionGitStatus,
} from '../../../api/sessionGitRepository';

// The project's branch rows, shadowed as in `GitScreen.test.tsx`: `items.list`
// answers the listing (or `items.error` fails it) and every project it was
// asked for is recorded.
const items = vi.hoisted(() => ({
  list: [] as GitItem[],
  error: undefined as Error | undefined,
  asked: [] as string[],
}));

vi.mock('../../../api/gitRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/gitRepository')>();
  return {
    ...actual,
    listGitItems: (_agentId: string, projectName: string) => {
      items.asked.push(projectName);
      return items.error === undefined ? Promise.resolve(items.list) : Promise.reject(items.error);
    },
  };
});

const { toApiError } = await import('../../../api/errors');
const { toSessionGitError } = await import('../../../api/sessionGitRepository');
const { GitRebaseDialog } = await import('./GitRebaseDialog');

const gitItem = (over: Partial<GitItem> = {}): GitItem => ({
  kind: 'branch_only',
  branch: 'feature-a',
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

const repo = (over: Partial<SessionGitRepoInfo> = {}): SessionGitRepoInfo => ({
  toplevel: '/srv/p1/.worktrees/topic',
  git_dir: '/srv/p1/.git/worktrees/topic',
  common_dir: '/srv/p1/.git',
  head: { oid: 'abc1234def', short_oid: 'abc1234', branch: 'topic', detached: false, initial: false },
  upstream: null,
  state: 'clean',
  is_linked_worktree: true,
  ...over,
});

const status = (over: Partial<SessionGitStatus> = {}): SessionGitStatus => ({
  session_id: 's1',
  cwd: '/srv/p1/.worktrees/topic',
  etag: 'e1',
  unchanged: false,
  unavailable_reason: null,
  repo: repo(),
  entries: [],
  counts: { conflicts: 0, staged: 0, unstaged: 0, untracked: 0, total: 0 },
  truncated: false,
  entry_limit: 5000,
  ...over,
});

const rebased: SessionGitRebaseResult = { outcome: 'rebased', rebase: null, message: null };

const wireFailure = (httpStatus: number, detail: unknown): unknown =>
  toSessionGitError(toApiError(new Response(null, { status: httpStatus }), { detail }));

const rebase = vi.fn<(onto: string) => Promise<SessionGitRebaseResult>>();
const onClose = vi.fn<() => void>();
const onDone = vi.fn<(result: SessionGitRebaseResult, onto: string) => void>();

let client: QueryClient;

type Props = Parameters<typeof GitRebaseDialog>[0];

const renderDialog = (over: Partial<Props> = {}) =>
  render(
    <QueryClientProvider client={client}>
      <GitRebaseDialog
        agentId="a1"
        projectName="p1"
        status={status()}
        busy={false}
        rebase={rebase}
        onClose={onClose}
        onDone={onDone}
        {...over}
      />
    </QueryClientProvider>,
  );

const dialog = (): HTMLElement => screen.getByRole('dialog', { name: /^Rebase/ });
const submit = (): HTMLElement => within(dialog()).getByRole('button', { name: 'Rebase' });
const optionLabels = (select: HTMLElement): string[] =>
  within(select)
    .getAllByRole('option')
    .map((option) => option.textContent ?? '');

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  items.list = [];
  items.error = undefined;
  items.asked.length = 0;
  rebase.mockResolvedValue(rebased);
});

afterEach(() => {
  cleanup();
  client.clear();
  rebase.mockReset();
  onClose.mockClear();
  onDone.mockClear();
});

describe('GitRebaseDialog picker', () => {
  it("offers the project's other branches, local first, with no preselection", async () => {
    items.list = [
      gitItem({ kind: 'remote_branch', branch: 'origin/main', remote: 'origin' }),
      gitItem({ branch: 'main', is_current: true }),
      gitItem({ kind: 'branch_with_worktree', branch: 'topic', worktree_path: '/srv/p1/.worktrees/topic' }),
    ];
    renderDialog();

    expect(dialog()).toHaveAccessibleName('Rebase topic');
    const select = within(dialog()).getByLabelText('Onto');
    await waitFor(() => {
      expect(optionLabels(select)).toEqual(['Choose a branch', 'main', 'origin/main (remote)']);
    });
    expect(select).toHaveValue('');
    expect(submit()).toBeDisabled();
    expect(items.asked).toEqual(['p1']);
  });

  it('rebases onto the picked branch and hands the result and the base back', async () => {
    items.list = [
      gitItem({ branch: 'main' }),
      gitItem({ kind: 'remote_branch', branch: 'origin/main', remote: 'origin' }),
    ];
    renderDialog();
    const select = within(dialog()).getByLabelText('Onto');
    await waitFor(() => {
      expect(select).toBeEnabled();
    });

    fireEvent.change(select, { target: { value: 'origin/main' } });
    fireEvent.click(submit());

    await waitFor(() => {
      expect(onDone).toHaveBeenCalledWith(rebased, 'origin/main');
    });
    expect(rebase.mock.calls).toEqual([['origin/main']]);
  });

  it('says so when the project has no other branch', async () => {
    items.list = [gitItem({ kind: 'branch_with_worktree', branch: 'topic', worktree_path: '/wt' })];
    renderDialog();

    expect(await within(dialog()).findByText('No other branches in this project.')).toBeInTheDocument();
    expect(submit()).toBeDisabled();
  });

  it('falls back to typing the ref when the branch list fails to load', async () => {
    items.error = new Error('offline');
    renderDialog();

    const field = await within(dialog()).findByPlaceholderText('Branch, tag or commit');
    fireEvent.change(field, { target: { value: 'v1.2.0' } });
    fireEvent.click(submit());

    await waitFor(() => {
      expect(rebase.mock.calls).toEqual([['v1.2.0']]);
    });
  });
});

describe('GitRebaseDialog typed ref', () => {
  it('types the ref for a session with no project, trimmed, and never lists branches', async () => {
    renderDialog({ projectName: null });

    const field = within(dialog()).getByLabelText('Onto');
    expect(field).toHaveAttribute('placeholder', 'Branch, tag or commit');
    expect(submit()).toBeDisabled();

    fireEvent.change(field, { target: { value: '   ' } });
    expect(submit()).toBeDisabled();

    fireEvent.change(field, { target: { value: ' origin/main ' } });
    fireEvent.click(submit());

    await waitFor(() => {
      expect(onDone).toHaveBeenCalledWith(rebased, 'origin/main');
    });
    expect(rebase.mock.calls).toEqual([['origin/main']]);
    expect(items.asked).toEqual([]);
  });
});

describe('GitRebaseDialog blockers and failures', () => {
  it('explains what is in the way and keeps Rebase disabled', () => {
    renderDialog({
      projectName: null,
      status: status({ counts: { conflicts: 0, staged: 1, unstaged: 0, untracked: 0, total: 1 } }),
    });
    fireEvent.change(within(dialog()).getByLabelText('Onto'), { target: { value: 'main' } });

    expect(within(dialog()).getByText('Commit or discard your changes first.')).toBeInTheDocument();
    expect(submit()).toBeDisabled();
  });

  it('lets untracked files through', () => {
    renderDialog({
      projectName: null,
      status: status({ counts: { conflicts: 0, staged: 0, unstaged: 0, untracked: 4, total: 4 } }),
    });
    fireEvent.change(within(dialog()).getByLabelText('Onto'), { target: { value: 'main' } });

    expect(submit()).toBeEnabled();
  });

  it("keeps a refusal inline with git's output and stays open", async () => {
    rebase.mockRejectedValue(
      wireFailure(409, { code: 'dirty_worktree', message: 'error: cannot rebase: You have unstaged changes.' }),
    );
    renderDialog({ projectName: null });
    fireEvent.change(within(dialog()).getByLabelText('Onto'), { target: { value: 'main' } });

    fireEvent.click(submit());

    const alert = await within(dialog()).findByRole('alert');
    expect(alert).toHaveTextContent(
      'Local changes are in the way — stage, commit or discard them first.',
    );
    expect(alert.querySelector('pre')).toHaveTextContent(
      'error: cannot rebase: You have unstaged changes.',
    );
    expect(onDone).not.toHaveBeenCalled();
    expect(dialog()).toBeInTheDocument();
  });

  it('names an unknown ref without any output block', async () => {
    rebase.mockRejectedValue(wireFailure(409, { code: 'unknown_ref', message: 'mian' }));
    renderDialog({ projectName: null });
    fireEvent.change(within(dialog()).getByLabelText('Onto'), { target: { value: 'mian' } });

    fireEvent.click(submit());

    const alert = await within(dialog()).findByRole('alert');
    expect(alert).toHaveTextContent('“mian” isn\'t a branch, tag or commit here.');
    expect(alert.querySelector('pre')).toBeNull();
  });

  it('locks the sheet while the rebase runs', () => {
    renderDialog({ projectName: null, busy: true });

    expect(within(dialog()).getByRole('button', { name: 'Rebasing…' })).toBeDisabled();
    expect(within(dialog()).getByRole('button', { name: 'Cancel' })).toBeDisabled();
    expect(within(dialog()).getByLabelText('Onto')).toBeDisabled();
  });
});

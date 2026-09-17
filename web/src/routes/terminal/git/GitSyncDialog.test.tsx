import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type {
  SessionGitJob,
  SessionGitRepoInfo,
  SessionGitSyncRequest,
} from '../../../api/sessionGitRepository';
import type { GitCapabilities } from '../../../protocol/dto/agentVersion';
import { GitSyncDialog, SyncProgressBar } from './GitSyncDialog';

const ALL_GIT: GitCapabilities = {
  stage: true,
  discard: true,
  commit: true,
  fetch: true,
  pull: true,
  push: true,
  branches: true,
  rebase: true,
  resolve: true,
};

const repo = (over: Partial<SessionGitRepoInfo> = {}): SessionGitRepoInfo => ({
  toplevel: '/srv/p1',
  git_dir: '/srv/p1/.git',
  common_dir: '/srv/p1/.git',
  head: { oid: 'abc1234def', short_oid: 'abc1234', branch: 'main', detached: false, initial: false },
  upstream: { ref: 'origin/main', ahead: 1, behind: 2 },
  state: 'clean',
  is_linked_worktree: false,
  ...over,
});

const job = (over: Partial<SessionGitJob> = {}): SessionGitJob => ({
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

const onStart = vi.fn<(request: SessionGitSyncRequest) => void>();
const onCancelJob = vi.fn<(jobId: string) => void>();
const onClose = vi.fn<() => void>();

type Props = Parameters<typeof GitSyncDialog>[0];

const renderDialog = (over: Partial<Props> = {}) =>
  render(
    <GitSyncDialog
      repo={repo()}
      capabilities={ALL_GIT}
      job={undefined}
      jobLost={false}
      starting={false}
      startError={null}
      onStart={onStart}
      onCancelJob={onCancelJob}
      onClose={onClose}
      {...over}
    />,
  );

const dialog = (): HTMLElement => screen.getByRole('dialog', { name: 'Fetch / Pull / Push' });
/** The row button behind a label; its detail is asserted as text. */
const row = (label: string): HTMLElement => {
  const button = within(dialog())
    .getByText(label, { selector: '.git-sync-row-label' })
    .closest('button');
  if (button === null) throw new Error(`no row labelled ${label}`);
  return button;
};

afterEach(() => {
  cleanup();
  onStart.mockClear();
  onCancelJob.mockClear();
  onClose.mockClear();
});

describe('GitSyncDialog rows', () => {
  it('shows the head and upstream line and starts each row with its default request', () => {
    renderDialog();

    expect(within(dialog()).getByText('main')).toBeInTheDocument();
    expect(within(dialog()).getByText('↑1 ↓2 · origin/main')).toBeInTheDocument();
    expect(row('Fetch')).toHaveTextContent('Update remote-tracking branches');
    expect(row('Pull')).toHaveTextContent('Fast-forward 2 commits from origin/main');
    expect(row('Push')).toHaveTextContent('1 commit to origin/main');

    fireEvent.click(row('Fetch'));
    fireEvent.click(row('Pull'));
    fireEvent.click(row('Push'));

    expect(onStart.mock.calls.map(([request]) => request)).toEqual([
      { op: 'fetch' },
      { op: 'pull', mode: 'ff_only' },
      { op: 'push', force: false },
    ]);
  });

  it('keeps a row that has nothing to do disabled with its reason', () => {
    renderDialog({ repo: repo({ upstream: { ref: 'origin/main', ahead: 0, behind: 0 } }) });

    expect(row('Pull')).toBeDisabled();
    expect(row('Pull')).toHaveTextContent('Nothing to pull');
    expect(row('Push')).toBeDisabled();
    expect(row('Push')).toHaveTextContent('Nothing to push');
    expect(row('Fetch')).toBeEnabled();
  });

  it('says No upstream and offers Publish branch on a branch nobody pushed yet', () => {
    renderDialog({ repo: repo({ upstream: null }) });

    expect(
      within(dialog()).getByText('No upstream', { selector: '.git-sync-upstream' }),
    ).toBeInTheDocument();
    expect(row('Publish branch')).toBeEnabled();
    expect(row('Publish branch')).toHaveTextContent('Push and set the upstream');
    expect(row('Pull')).toBeDisabled();
    expect(row('Pull')).toHaveTextContent('No upstream');
  });

  it('shows only the rows the agent advertises', () => {
    renderDialog({ capabilities: { ...ALL_GIT, pull: false, push: false } });

    expect(
      [...dialog().querySelectorAll('.git-sync-row-label')].map((label) => label.textContent),
    ).toEqual(['Fetch']);
    expect(within(dialog()).getAllByRole('button')).toHaveLength(2);
  });

  it('locks every row and Close while the start request is in flight', () => {
    renderDialog({ starting: true });

    expect(row('Fetch')).toBeDisabled();
    expect(row('Pull')).toBeDisabled();
    expect(row('Push')).toBeDisabled();
    expect(within(dialog()).getByRole('button', { name: 'Close' })).toBeDisabled();
  });

  it('ignores Escape while starting and closes on it otherwise', () => {
    const view = renderDialog({ starting: true });

    fireEvent(dialog(), new Event('cancel', { cancelable: true }));
    expect(onClose).not.toHaveBeenCalled();

    view.rerender(
      <GitSyncDialog
        repo={repo()}
        capabilities={ALL_GIT}
        job={undefined}
        jobLost={false}
        starting={false}
        startError={null}
        onStart={onStart}
        onCancelJob={onCancelJob}
        onClose={onClose}
      />,
    );
    fireEvent(dialog(), new Event('cancel', { cancelable: true }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('reports a start refusal and a lost job as alerts', () => {
    renderDialog({ startError: 'Another fetch, pull or push is still running.', jobLost: true });

    expect(within(dialog()).getAllByRole('alert').map((alert) => alert.textContent)).toEqual([
      'Another fetch, pull or push is still running.',
      'Lost track of the job — refresh to see the result.',
    ]);
  });
});

describe('GitSyncDialog while a job runs', () => {
  it('shows the title, phase, bar and last line, locks the rows and offers Cancel', () => {
    renderDialog({
      job: job({ phase: 'Writing objects', percent: 45, line: 'Writing objects:  45% (14/32)' }),
    });

    const progress = within(dialog()).getByRole('status');
    expect(progress).toHaveTextContent('Pushing…');
    expect(progress).toHaveTextContent('Writing objects 45%');
    expect(within(progress).getByRole('progressbar', { name: 'Writing objects' })).toHaveAttribute(
      'aria-valuenow',
      '45',
    );
    expect(progress.querySelector('code.git-sync-line')).toHaveTextContent(
      'Writing objects:  45% (14/32)',
      { normalizeWhitespace: false },
    );
    expect(row('Fetch')).toBeDisabled();
    expect(row('Push')).toBeDisabled();
    expect(within(dialog()).getByRole('button', { name: 'Close' })).toBeEnabled();

    fireEvent.click(within(progress).getByRole('button', { name: 'Cancel' }));

    expect(onCancelJob).toHaveBeenCalledWith('j1');
  });

  it('draws the indeterminate block before git reports a phase', () => {
    renderDialog({ job: job({ op: 'fetch' }) });

    const progress = within(dialog()).getByRole('status');
    expect(progress).toHaveTextContent('Fetching…');
    expect(within(progress).queryByRole('progressbar')).toBeNull();
    expect(progress.querySelector('.git-sync-bar-indeterminate')).toHaveAttribute(
      'aria-label',
      'Fetching…',
    );
  });
});

describe('GitSyncDialog once a job is done', () => {
  it('reports a success with its summary and frees the rows', () => {
    renderDialog({ job: job({ status: 'done', ok: true, message: 'abc1234..def5678 main -> main' }) });

    const result = within(dialog()).getByRole('status');
    expect(result).toHaveTextContent('Pushed');
    expect(within(result).getByText('abc1234..def5678 main -> main')).toBeInTheDocument();
    expect(row('Fetch')).toBeEnabled();
    expect(within(dialog()).queryByRole('button', { name: 'Cancel' })).toBeNull();
  });

  it('confirms a force push with Cancel focused, then starts it with the lease', () => {
    renderDialog({
      job: job({
        status: 'done',
        ok: false,
        error_code: 'rejected',
        message: '! [rejected] main -> main (fetch first)',
      }),
    });

    const result = within(dialog()).getByRole('alert');
    expect(result).toHaveTextContent('Push failed');
    expect(result).toHaveTextContent('The remote rejected the push.');
    expect(within(result).getByText('! [rejected] main -> main (fetch first)')).toBeInTheDocument();

    fireEvent.click(within(result).getByRole('button', { name: 'Force push (with lease)…' }));

    const confirm = screen.getByRole('alertdialog', { name: 'Force push?' });
    expect(confirm).toHaveTextContent(
      "Overwrites origin/main with your branch. Proceeds only if the remote hasn't moved since your last fetch.",
    );
    expect(within(confirm).getByRole('button', { name: 'Cancel' })).toHaveFocus();

    fireEvent.click(within(confirm).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(onStart).not.toHaveBeenCalled();

    fireEvent.click(within(result).getByRole('button', { name: 'Force push (with lease)…' }));
    fireEvent.click(
      within(screen.getByRole('alertdialog', { name: 'Force push?' })).getByRole('button', {
        name: 'Force push',
      }),
    );

    expect(onStart).toHaveBeenCalledWith({ op: 'push', force: true });
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('offers rebase and merge after a pull that could not fast-forward', () => {
    renderDialog({
      job: job({
        op: 'pull',
        status: 'done',
        ok: false,
        error_code: 'not_fast_forward',
        message: 'fatal: Not possible to fast-forward, aborting.',
      }),
    });

    const result = within(dialog()).getByRole('alert');
    expect(result).toHaveTextContent('Pull failed');
    expect(result).toHaveTextContent('Your branch is behind the remote.');
    expect(within(result).getByRole('button', { name: 'Pull with merge…' })).toBeInTheDocument();

    fireEvent.click(within(result).getByRole('button', { name: 'Pull with rebase…' }));
    const confirm = screen.getByRole('alertdialog', { name: 'Pull with rebase?' });
    expect(confirm).toHaveTextContent('Replays your commits on top of origin/main.');
    fireEvent.click(within(confirm).getByRole('button', { name: 'Rebase' }));

    expect(onStart).toHaveBeenCalledWith({ op: 'pull', mode: 'rebase' });
  });

  it('offers only the lease push after a refused publish, since the branch has no upstream to pull', () => {
    renderDialog({
      repo: repo({ upstream: null }),
      job: job({
        status: 'done',
        ok: false,
        error_code: 'not_fast_forward',
        message: '! [rejected] main -> main (fetch first)',
      }),
    });

    const result = within(dialog()).getByRole('alert');
    expect(result).toHaveTextContent('Push failed');
    expect(within(result).getByRole('button', { name: 'Force push (with lease)…' })).toBeInTheDocument();
    expect(within(result).queryByRole('button', { name: 'Pull with rebase…' })).toBeNull();
    expect(within(result).queryByRole('button', { name: 'Pull with merge…' })).toBeNull();
  });

  it('shows no follow-up and no duplicate output when the copy already says it all', () => {
    renderDialog({
      job: job({
        op: 'fetch',
        status: 'done',
        ok: false,
        error_code: 'git_error',
        message: 'fatal: boom',
      }),
    });

    const result = within(dialog()).getByRole('alert');
    expect(result).toHaveTextContent('Fetch failed');
    expect(within(result).getAllByText('fatal: boom')).toHaveLength(1);
    expect(within(result).queryByRole('button')).toBeNull();
  });
});

describe('SyncProgressBar', () => {
  it('is a determinate bar for a percentage, clamped to the scale', () => {
    render(<SyncProgressBar percent={150} label="Writing objects" />);

    const bar = screen.getByRole('progressbar', { name: 'Writing objects' });
    expect(bar).toHaveAttribute('aria-valuenow', '100');
    expect(bar).toHaveAttribute('aria-valuemin', '0');
    expect(bar).toHaveAttribute('aria-valuemax', '100');
    expect(bar.querySelector('.git-sync-bar-fill')).toHaveStyle({ width: '100%' });
  });

  it('is the blinking block, not a progressbar, without a percentage', () => {
    render(<SyncProgressBar percent={null} label="Pushing…" />);

    expect(screen.queryByRole('progressbar')).toBeNull();
    expect(document.querySelector('.git-sync-bar-indeterminate')).toHaveClass('state-loading');
  });
});

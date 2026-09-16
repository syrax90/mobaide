import { describe, expect, it } from 'vitest';
import { toApiError } from '../../../api/errors';
import type { GitDeleteResult, GitItem } from '../../../api/gitRepository';
import { toSessionGitError } from '../../../api/sessionGitRepository';
import {
  branchCreatedMessage,
  checkedOutMessage,
  checkoutConfirmMessage,
  checkoutErrorCopy,
  chips,
  createBranchErrorCopy,
  deleteFailureMessage,
  deleteFailureReason,
  matchesFilter,
  primaryLabel,
  secondaryLabel,
  shortDate,
  tipLine,
} from './gitDisplay';

const item = (over: Partial<GitItem> = {}): GitItem => ({
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

const remoteBranch = (over: Partial<GitItem> = {}): GitItem =>
  item({ kind: 'remote_branch', branch: 'origin/feature-a', remote: 'origin', ...over });

/** A failure as the repository throws it off the wire. */
const wireFailure = (status: number, detail: unknown): unknown =>
  toSessionGitError(toApiError(new Response(null, { status }), { detail }));

const failure = (over: Partial<GitDeleteResult> = {}): GitDeleteResult => ({
  kind: 'branch_only',
  target: 'feature-a',
  ok: false,
  ...over,
});

describe('labels', () => {
  it('titles a worktree-less branch with its name and nothing else', () => {
    expect(primaryLabel(item())).toBe('feature-a');
    expect(secondaryLabel(item())).toBeNull();
  });

  it('titles a branch with a worktree by name, with the path underneath', () => {
    const row = item({ kind: 'branch_with_worktree', worktree_path: '/wt/a' });

    expect(primaryLabel(row)).toBe('feature-a');
    expect(secondaryLabel(row)).toBe('/wt/a');
  });

  it('titles an orphan worktree by its path, and marks a detached head', () => {
    const row = item({ kind: 'orphan_worktree', branch: null, worktree_path: '/wt/x' });

    expect(primaryLabel(row)).toBe('/wt/x');
    expect(secondaryLabel(row)).toBeNull();
    expect(secondaryLabel({ ...row, detached: true })).toBe('(detached HEAD)');
  });
});

describe('chips', () => {
  it('renders nothing for an ordinary branch', () => {
    expect(chips(item())).toEqual([]);
  });

  it('keeps the fixed order regardless of which flags are set', () => {
    const row = item({
      kind: 'orphan_worktree',
      branch: null,
      worktree_path: '/wt/x',
      gone: true,
      ahead: 2,
      behind: 1,
      locked: true,
      locked_reason: '  in use  ',
      prunable: true,
    });

    expect(chips(row)).toEqual([
      { text: 'gone', style: 'danger' },
      { text: 'ahead 2 / behind 1', style: 'neutral' },
      { text: 'locked: in use', style: 'danger' },
      { text: 'orphan', style: 'danger' },
      { text: 'prunable', style: 'muted' },
    ]);
  });

  it('marks the checked-out branch first', () => {
    expect(chips(item({ is_current: true, behind: 3 }))).toEqual([
      { text: 'current', style: 'highlight' },
      { text: 'ahead 0 / behind 3', style: 'neutral' },
    ]);
  });

  it('falls back to a bare locked chip when git gave no reason', () => {
    expect(chips(item({ locked: true, locked_reason: '   ' }))).toEqual([
      { text: 'locked', style: 'danger' },
    ]);
  });

  it('names the remote on a remote-tracking row, after current and before gone', () => {
    expect(chips(remoteBranch())).toEqual([{ text: 'origin', style: 'muted' }]);
    expect(chips(remoteBranch({ remote: null, gone: true }))).toEqual([
      { text: 'remote', style: 'muted' },
      { text: 'gone', style: 'danger' },
    ]);
  });
});

describe('shortDate', () => {
  it('keeps the committer-zone day as a prefix of the iso-strict date', () => {
    expect(shortDate('2026-09-16T10:20:30+02:00')).toBe('2026-09-16');
    expect(shortDate('2026-09-16')).toBe('2026-09-16');
  });

  it('is null for a missing or empty date', () => {
    expect(shortDate(null)).toBeNull();
    expect(shortDate(undefined)).toBeNull();
    expect(shortDate('')).toBeNull();
  });
});

describe('tipLine', () => {
  it('joins the subject and the day with a middle dot', () => {
    expect(tipLine(item({ subject: '  Fix login  ', committed_at: '2026-09-16T10:20:30+02:00' }))).toBe(
      'Fix login · 2026-09-16',
    );
  });

  it('shows whichever half the agent reported', () => {
    expect(tipLine(item({ subject: 'Fix login' }))).toBe('Fix login');
    expect(tipLine(item({ committed_at: '2026-09-16T10:20:30+02:00' }))).toBe('2026-09-16');
  });

  it('is null when the ref has no tip commit', () => {
    expect(tipLine(item())).toBeNull();
    expect(tipLine(item({ subject: '   ' }))).toBeNull();
  });
});

describe('matchesFilter', () => {
  it('matches everything on an empty or blank filter', () => {
    expect(matchesFilter(item(), '')).toBe(true);
    expect(matchesFilter(item(), '   ')).toBe(true);
  });

  it('matches the label case-insensitively and the worktree path', () => {
    expect(matchesFilter(item({ branch: 'Feature/Login' }), 'login')).toBe(true);
    expect(matchesFilter(item({ branch: 'Feature/Login' }), ' FEAT ')).toBe(true);
    expect(matchesFilter(remoteBranch(), 'origin/')).toBe(true);
    expect(
      matchesFilter(item({ kind: 'branch_with_worktree', worktree_path: '/wt/Alpha' }), 'alpha'),
    ).toBe(true);
    expect(
      matchesFilter(item({ kind: 'orphan_worktree', branch: null, worktree_path: '/wt/x' }), 'wt/x'),
    ).toBe(true);
  });

  it('rejects a row that matches nowhere', () => {
    expect(matchesFilter(item(), 'main')).toBe(false);
  });
});

describe('messages', () => {
  it('spells out what a checkout in the project folder means', () => {
    expect(checkoutConfirmMessage('feature-a', 'proj')).toBe(
      'Check out feature-a in the project folder of proj? ' +
        'Sessions started in that folder afterwards use this branch.',
    );
  });

  it('reports the branch the agent touched', () => {
    expect(checkedOutMessage({ branch: 'feature-a', checked_out: true, created: false })).toBe(
      'Checked out feature-a',
    );
    expect(branchCreatedMessage({ branch: 'feature-a', checked_out: false, created: true })).toBe(
      'Created feature-a',
    );
    expect(branchCreatedMessage({ branch: 'feature-a', checked_out: true, created: true })).toBe(
      'Created and checked out feature-a',
    );
  });
});

describe('checkoutErrorCopy', () => {
  it.each([
    [
      { code: 'dirty', message: 'error: Your local changes' },
      'The project folder has local changes this checkout would overwrite. ' +
        "Commit or discard them from a session's Changes screen first.",
    ],
    [
      { code: 'checked_out_elsewhere', message: '/wt/topic' },
      'feature-a is already checked out in another worktree (/wt/topic).',
    ],
    [
      { code: 'checked_out_elsewhere' },
      'feature-a is already checked out in another worktree.',
    ],
    [
      { code: 'operation_in_progress', message: 'merging' },
      'A merge, rebase or cherry-pick is in progress in the project folder. ' +
        'Finish or abort it first.',
    ],
    [
      { code: 'job_running', message: 'job-1' },
      'A fetch, pull or push is still running in the project folder.',
    ],
  ])('words the %j refusal', (detail, expected) => {
    expect(checkoutErrorCopy(wireFailure(409, detail), 'feature-a')).toBe(expected);
  });

  it('tells the user to refresh when the ref vanished', () => {
    expect(checkoutErrorCopy(wireFailure(404, { code: 'ref_not_found' }), 'feature-a')).toBe(
      'Branch not found — refresh the list.',
    );
  });

  it('falls back to the session-git copy for everything else', () => {
    expect(
      checkoutErrorCopy(wireFailure(500, { code: 'git_error', message: 'fatal: boom' }), 'x'),
    ).toBe('git failed: fatal: boom');
    expect(checkoutErrorCopy(wireFailure(404, 'Not Found'), 'x')).toBe(
      'Update your agent to view changes.',
    );
    expect(checkoutErrorCopy(new Error('offline'), 'x')).toBe('offline');
  });
});

describe('createBranchErrorCopy', () => {
  it('names the clash, the bad name and the missing start point', () => {
    expect(
      createBranchErrorCopy(wireFailure(409, { code: 'exists', message: 'feature-a' }), 'feature-a'),
    ).toBe('A branch named feature-a already exists.');
    expect(createBranchErrorCopy(wireFailure(400, { code: 'bad_branch_name' }), '-x')).toBe(
      'Not a valid branch name.',
    );
    expect(
      createBranchErrorCopy(
        wireFailure(409, { code: 'unknown_start_point', message: 'nope' }),
        'feature-a',
      ),
    ).toBe('Start point nope was not found.');
    expect(
      createBranchErrorCopy(wireFailure(409, { code: 'unknown_start_point' }), 'feature-a'),
    ).toBe('The start point was not found.');
  });

  it('hands the checkout refusals of a create-and-checkout to the checkout copy', () => {
    expect(
      createBranchErrorCopy(wireFailure(409, { code: 'checked_out_elsewhere', message: '/wt' }), 'feature-a'),
    ).toBe('feature-a is already checked out in another worktree (/wt).');
  });
});

describe('deleteFailureReason', () => {
  it('shows the first line of raw git stderr', () => {
    expect(
      deleteFailureReason(
        failure({ error: 'git_failed', error_detail: '  fatal: bad object  \nhint: try again' }),
      ),
    ).toBe('fatal: bad object');
  });

  it('caps a one-line wall of stderr at 120 characters', () => {
    const reason = deleteFailureReason(failure({ error_detail: 'x'.repeat(200) }));

    expect(reason).toHaveLength(120);
  });

  it.each([
    ['locked', 'the worktree is locked'],
    ['not_found', 'it no longer exists'],
    ['invalid', 'the request was invalid'],
    ['something_new', 'unknown error'],
  ])('falls back to the %s wording when stderr is empty', (error, expected) => {
    expect(deleteFailureReason(failure({ error, error_detail: '   ' }))).toBe(expected);
  });
});

describe('deleteFailureMessage', () => {
  it('names the item when exactly one failed', () => {
    expect(deleteFailureMessage([failure({ error: 'locked' })])).toBe(
      "Couldn't delete feature-a: the worktree is locked",
    );
  });

  it('counts them instead when several failed', () => {
    expect(deleteFailureMessage([failure(), failure({ target: 'feature-b' })])).toBe(
      '2 item(s) failed to delete',
    );
  });
});

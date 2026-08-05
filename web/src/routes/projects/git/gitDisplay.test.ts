import { describe, expect, it } from 'vitest';
import type { GitDeleteResult, GitItem } from '../../../api/gitRepository';
import {
  chips,
  deleteFailureMessage,
  deleteFailureReason,
  primaryLabel,
  secondaryLabel,
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
  ...over,
});

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

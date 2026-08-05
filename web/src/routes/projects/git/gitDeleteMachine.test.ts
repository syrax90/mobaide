import { describe, expect, it } from 'vitest';
import {
  gitItemId,
  type GitDeleteResult,
  type GitItem,
} from '../../../api/gitRepository';
import {
  applyResults,
  confirmForce,
  dismissForce,
  enqueue,
  initialGitDeleteState,
  reconcile,
  restore,
  takePending,
  undo,
  visibleItems,
  type GitDeleteState,
} from './gitDeleteMachine';

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
  ...over,
});

const result = (target: string, over: Partial<GitDeleteResult> = {}): GitDeleteResult => ({
  kind: 'branch_only',
  target,
  ok: true,
  ...over,
});

const hidden = (state: GitDeleteState): string[] => [...state.hidden].sort();

describe('enqueue', () => {
  it('hides the queued rows and remembers what to send', () => {
    const items = [branch('feature-a'), branch('feature-b')];

    const state = enqueue(initialGitDeleteState, [items[0] as GitItem]);

    expect(visibleItems(items, state).map((item) => item.branch)).toEqual(['feature-b']);
    expect(state.pending).toEqual([
      {
        id: 'branch_only:feature-a',
        item: { kind: 'branch_only', branch: 'feature-a', worktree_path: null, force: false },
      },
    ]);
  });

  it('appends to an open batch instead of replacing it', () => {
    const first = enqueue(initialGitDeleteState, [branch('feature-a')]);

    const second = enqueue(first, [branch('feature-b')]);

    expect(second.pending.map((entry) => entry.id)).toEqual([
      'branch_only:feature-a',
      'branch_only:feature-b',
    ]);
  });

  it('ignores the checked-out branch and rows already queued', () => {
    const queued = enqueue(initialGitDeleteState, [branch('feature-a')]);

    const again = enqueue(queued, [branch('feature-a'), branch('main', { is_current: true })]);

    expect(again).toBe(queued);
  });
});

describe('undo', () => {
  it('un-hides the batch and sends nothing', () => {
    const items = [branch('feature-a'), branch('feature-b')];
    const queued = enqueue(initialGitDeleteState, items);

    const state = undo(queued);

    expect(visibleItems(items, state)).toEqual(items);
    expect(state.pending).toEqual([]);
  });
});

describe('takePending', () => {
  it('drains the batch while the rows stay hidden', () => {
    const queued = enqueue(initialGitDeleteState, [branch('feature-a')]);

    const { state, batch } = takePending(queued);

    expect(batch.map((entry) => entry.id)).toEqual(['branch_only:feature-a']);
    expect(state.pending).toEqual([]);
    expect(hidden(state)).toEqual(['branch_only:feature-a']);
  });
});

describe('applyResults', () => {
  const queueThree = () => {
    const items = [branch('a'), branch('b'), branch('c')];
    return { items, ...takePending(enqueue(initialGitDeleteState, items)) };
  };

  it('leaves every deleted row hidden and prompts for nothing', () => {
    const { state, batch } = queueThree();

    const outcome = applyResults(state, batch, [result('a'), result('b'), result('c')]);

    expect(hidden(outcome.state)).toEqual([
      'branch_only:a',
      'branch_only:b',
      'branch_only:c',
    ]);
    expect(outcome.failures).toEqual([]);
    expect(outcome.state.force).toBeNull();
  });

  it('maps results positionally, restoring the force_required row it names', () => {
    const { state, batch } = queueThree();

    const outcome = applyResults(state, batch, [
      result('a'),
      result('b', { ok: false, error: 'force_required' }),
      result('c'),
    ]);

    expect(hidden(outcome.state)).toEqual(['branch_only:a', 'branch_only:c']);
    expect(outcome.state.force?.refs).toEqual(['b']);
    expect(outcome.state.force?.items.map((entry) => entry.id)).toEqual(['branch_only:b']);
    expect(outcome.failures).toEqual([]);
  });

  it('restores and reports a failure the user cannot retry away', () => {
    const { state, batch } = queueThree();

    const outcome = applyResults(state, batch, [
      result('a'),
      result('b'),
      result('c', { ok: false, error: 'locked' }),
    ]);

    expect(hidden(outcome.state)).toEqual(['branch_only:a', 'branch_only:b']);
    expect(outcome.failures.map((failure) => failure.target)).toEqual(['c']);
    expect(outcome.state.force).toBeNull();
  });

  it('restores the tail a short results array never reported', () => {
    const { state, batch } = queueThree();

    const outcome = applyResults(state, batch, [result('a')]);

    expect(hidden(outcome.state)).toEqual(['branch_only:a']);
    expect(outcome.failures).toEqual([]);
  });

  it('ignores results the request never asked for', () => {
    const { state, batch } = queueThree();

    const outcome = applyResults(state, batch, [
      result('a'),
      result('b'),
      result('c'),
      result('ghost', { ok: false, error: 'not_found' }),
    ]);

    expect(hidden(outcome.state)).toEqual([
      'branch_only:a',
      'branch_only:b',
      'branch_only:c',
    ]);
    expect(outcome.failures).toEqual([]);
  });
});

describe('confirmForce', () => {
  it('re-hides the prompted rows and re-sends them with force set', () => {
    const items = [branch('a'), branch('b')];
    const taken = takePending(enqueue(initialGitDeleteState, items));
    const prompted = applyResults(taken.state, taken.batch, [
      result('a'),
      result('b', { ok: false, error: 'force_required' }),
    ]).state;

    const { state, batch } = confirmForce(prompted);

    expect(batch).toEqual([
      {
        id: 'branch_only:b',
        item: { kind: 'branch_only', branch: 'b', worktree_path: null, force: true },
      },
    ]);
    expect(hidden(state)).toEqual(['branch_only:a', 'branch_only:b']);
    expect(state.force).toBeNull();
  });

  it('does nothing when no prompt is open', () => {
    const { state, batch } = confirmForce(initialGitDeleteState);

    expect(state).toBe(initialGitDeleteState);
    expect(batch).toEqual([]);
  });

  it('leaves the rows visible when the prompt is dismissed', () => {
    const items = [branch('a')];
    const taken = takePending(enqueue(initialGitDeleteState, items));
    const prompted = applyResults(taken.state, taken.batch, [
      result('a', { ok: false, error: 'force_required' }),
    ]).state;

    const state = dismissForce(prompted);

    expect(state.force).toBeNull();
    expect(visibleItems(items, state)).toEqual(items);
  });
});

describe('restore', () => {
  it('brings back the whole batch after an all-or-nothing busy conflict', () => {
    const items = [branch('a'), branch('b')];
    const { state, batch } = takePending(enqueue(initialGitDeleteState, items));

    expect(visibleItems(items, restore(state, batch))).toEqual(items);
  });
});

describe('reconcile', () => {
  it('forgets rows the server no longer lists, in both the hide-set and the batch', () => {
    const queued = enqueue(initialGitDeleteState, [branch('a'), branch('b')]);

    const state = reconcile(queued, [branch('a')]);

    expect(hidden(state)).toEqual(['branch_only:a']);
    expect(state.pending.map((entry) => entry.id)).toEqual(['branch_only:a']);
  });

  it('returns the same state when the reload changed nothing', () => {
    const queued = enqueue(initialGitDeleteState, [branch('a')]);

    expect(reconcile(queued, [branch('a'), branch('b')])).toBe(queued);
  });
});

describe('gitItemId', () => {
  it('keys a worktree row on its path, so two branches sharing a name stay distinct', () => {
    expect(gitItemId(branch('a'))).toBe('branch_only:a');
    expect(
      gitItemId({ kind: 'orphan_worktree', branch: null, worktree_path: '/wt/a' }),
    ).toBe('orphan_worktree:/wt/a');
    expect(
      gitItemId({ kind: 'branch_with_worktree', branch: 'a', worktree_path: '/wt/a' }),
    ).toBe('branch_with_worktree:a');
  });
});

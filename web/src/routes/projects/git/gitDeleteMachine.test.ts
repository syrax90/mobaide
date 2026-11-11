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
  enqueueWorktreeRemoval,
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
  remote: null,
  subject: null,
  committed_at: null,
  ...over,
});

const remoteBranch = (name: string): GitItem =>
  branch(`origin/${name}`, { kind: 'remote_branch', remote: 'origin' });

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

    const state = enqueue(initialGitDeleteState, [items[0] as GitItem], false);

    expect(visibleItems(items, state).map((item) => item.branch)).toEqual(['feature-b']);
    expect(state.pending).toEqual([
      {
        id: 'branch_only:feature-a',
        item: { kind: 'branch_only', branch: 'feature-a', worktree_path: null, force: false },
      },
    ]);
  });

  it('ignores the checked-out branch and rows already queued', () => {
    const queued = enqueue(initialGitDeleteState, [branch('feature-a')], false);

    const again = enqueue(queued, [branch('feature-a'), branch('main', { is_current: true })], false);

    expect(again).toBe(queued);
  });

  it('never lets a remote-tracking row into a batch', () => {
    const items = [remoteBranch('feature-a'), branch('feature-b')];

    expect(enqueue(initialGitDeleteState, [items[0] as GitItem], false)).toBe(initialGitDeleteState);

    const mixed = enqueue(initialGitDeleteState, items, false);
    expect(mixed.pending.map((entry) => entry.id)).toEqual(['branch_only:feature-b']);
    expect(visibleItems(items, mixed).map((item) => item.branch)).toEqual(['origin/feature-a']);
  });
});

describe('undo', () => {
  it('un-hides the batch and sends nothing', () => {
    const items = [branch('feature-a'), branch('feature-b')];
    const queued = enqueue(initialGitDeleteState, items, false);

    const state = undo(queued);

    expect(visibleItems(items, state)).toEqual(items);
    expect(state.pending).toEqual([]);
  });
});

describe('takePending', () => {
  it('drains the batch while the rows stay hidden', () => {
    const queued = enqueue(initialGitDeleteState, [branch('feature-a')], false);

    const { state, batch } = takePending(queued);

    expect(batch.map((entry) => entry.id)).toEqual(['branch_only:feature-a']);
    expect(state.pending).toEqual([]);
    expect(hidden(state)).toEqual(['branch_only:feature-a']);
  });
});

describe('applyResults', () => {
  const queueThree = () => {
    const items = [branch('a'), branch('b'), branch('c')];
    return { items, ...takePending(enqueue(initialGitDeleteState, items, false)) };
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
    const taken = takePending(enqueue(initialGitDeleteState, items, false));
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

  it('leaves the rows visible when the prompt is dismissed', () => {
    const items = [branch('a')];
    const taken = takePending(enqueue(initialGitDeleteState, items, false));
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
    const { state, batch } = takePending(enqueue(initialGitDeleteState, items, false));

    expect(visibleItems(items, restore(state, batch))).toEqual(items);
  });
});

describe('reconcile', () => {
  it('forgets rows the server no longer lists, in both the hide-set and the batch', () => {
    const queued = enqueue(initialGitDeleteState, [branch('a'), branch('b')], false);

    const state = reconcile(queued, [branch('a')]);

    expect(hidden(state)).toEqual(['branch_only:a']);
    expect(state.pending.map((entry) => entry.id)).toEqual(['branch_only:a']);
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

  it('keys a remote-tracking row on the kind and its remote-qualified name', () => {
    expect(gitItemId(remoteBranch('a'))).toBe('remote_branch:origin/a|');
    expect(gitItemId(remoteBranch('a'))).not.toBe(gitItemId(branch('origin/a')));
  });
});

describe('enqueue with delete_remote', () => {
  it('lets a remote-tracking row in, sent as its remote-qualified name', () => {
    const items = [remoteBranch('feature-a'), branch('feature-b')];

    const state = enqueue(initialGitDeleteState, items, true);

    expect(state.pending).toEqual([
      {
        id: 'remote_branch:origin/feature-a|',
        item: { kind: 'remote_branch', branch: 'origin/feature-a', worktree_path: null, force: false },
      },
      {
        id: 'branch_only:feature-b',
        item: { kind: 'branch_only', branch: 'feature-b', worktree_path: null, force: false },
      },
    ]);
    expect(visibleItems(items, state)).toEqual([]);
  });
});

describe('enqueueWorktreeRemoval', () => {
  const topic = branch('topic', { kind: 'branch_with_worktree', worktree_path: '/wt/topic' });
  const worktreeOnly = { kind: 'orphan_worktree', branch: null, worktree_path: '/wt/topic' } as const;

  it("hides the row and queues only its worktree under the row's id", () => {
    const items = [topic, branch('feature-a')];

    const state = enqueueWorktreeRemoval(initialGitDeleteState, topic);

    expect(state.pending).toEqual([
      { id: 'branch_with_worktree:topic', item: { ...worktreeOnly, force: false } },
    ]);
    expect(visibleItems(items, state).map((item) => item.branch)).toEqual(['feature-a']);
  });

  it('joins an open batch and ignores rows without a removable worktree', () => {
    const queued = enqueue(initialGitDeleteState, [branch('feature-a')], false);

    const state = enqueueWorktreeRemoval(queued, topic);

    expect(state.pending.map((entry) => entry.id)).toEqual([
      'branch_only:feature-a',
      'branch_with_worktree:topic',
    ]);
    expect(enqueueWorktreeRemoval(state, topic)).toBe(state);
    expect(enqueueWorktreeRemoval(queued, branch('feature-b'))).toBe(queued);
    expect(enqueueWorktreeRemoval(queued, { ...topic, is_current: true })).toBe(queued);
    expect(
      enqueueWorktreeRemoval(queued, branch('x', { kind: 'orphan_worktree', worktree_path: '/wt/x' })),
    ).toBe(queued);
  });

  it('prompts by path for a dirty worktree and re-sends only the worktree removal with force', () => {
    const taken = takePending(enqueueWorktreeRemoval(initialGitDeleteState, topic));
    const prompted = applyResults(taken.state, taken.batch, [
      result('/wt/topic', { kind: 'orphan_worktree', ok: false, error: 'force_required' }),
    ]).state;

    expect(prompted.force?.refs).toEqual(['/wt/topic']);

    const { batch } = confirmForce(prompted);

    expect(batch).toEqual([
      { id: 'branch_with_worktree:topic', item: { ...worktreeOnly, force: true } },
    ]);
  });
});

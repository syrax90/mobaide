/**
 * Deferred git delete, as a pure state machine.
 *
 * Ported from iOS `GitScreenModel` / Android `GitViewModel`, whose invariants are
 * subtle enough to be worth restating:
 *
 * * Queued rows are **hidden**, never removed. The list is server-authoritative;
 *   a removal would be a local edit the next reload could not reconcile.
 * * The pending batch lives outside the timer. A second delete inside an open
 *   window joins the batch and restarts the countdown, and holding the batch in
 *   the timer's closure would let that restart discard the first delete.
 * * `results` maps **positionally** onto the request, which is why each pending
 *   entry pairs the wire item with the row id it came from.
 * * The busy 409 is all-or-nothing — the guard runs before any deletion — so it
 *   restores the whole batch, not the reported subset.
 */
import type {
  DeletableGitItem,
  GitDeleteItem,
  GitDeleteResult,
  GitItem,
} from '../../../api/gitRepository';
import {
  canRemoveWorktree,
  gitItemId,
  isDeletable,
  KIND_ORPHAN_WORKTREE,
} from '../../../api/gitRepository';

export interface PendingDelete {
  id: string;
  item: GitDeleteItem;
}

export interface ForcePrompt {
  items: PendingDelete[];
  /** The branch or worktree names the confirm dialog lists. */
  refs: string[];
}

export interface GitDeleteState {
  hidden: ReadonlySet<string>;
  pending: readonly PendingDelete[];
  force: ForcePrompt | null;
}

export const initialGitDeleteState: GitDeleteState = {
  hidden: new Set(),
  pending: [],
  force: null,
};

function toDeleteItem(item: DeletableGitItem): GitDeleteItem {
  return {
    kind: item.kind,
    branch: item.branch ?? null,
    worktree_path: item.worktree_path ?? null,
    force: false,
  };
}

export function visibleItems(
  items: readonly GitItem[],
  state: GitDeleteState,
): GitItem[] {
  return items.filter((item) => !state.hidden.has(gitItemId(item)));
}

/**
 * *Remove worktree*: an `orphan_worktree` item removes only the worktree at
 * its path, whatever row it came from, so the branch survives.
 */
function toWorktreeRemoval(worktreePath: string): GitDeleteItem {
  return { kind: KIND_ORPHAN_WORKTREE, branch: null, worktree_path: worktreePath, force: false };
}

function enqueueEntries(state: GitDeleteState, entries: readonly PendingDelete[]): GitDeleteState {
  const queued = entries.filter((entry) => !state.hidden.has(entry.id));
  if (queued.length === 0) return state;

  const hidden = new Set(state.hidden);
  const pending = [...state.pending];
  for (const entry of queued) {
    hidden.add(entry.id);
    pending.push(entry);
  }
  return { ...state, hidden, pending };
}

/**
 * Queue rows for deletion. Already-hidden and non-deletable targets are
 * ignored; `deleteRemote` is `capabilities.git.delete_remote`.
 */
export function enqueue(
  state: GitDeleteState,
  targets: readonly GitItem[],
  deleteRemote: boolean,
): GitDeleteState {
  return enqueueEntries(
    state,
    targets
      .filter((item) => isDeletable(item, deleteRemote))
      .map((item) => ({ id: gitItemId(item), item: toDeleteItem(item) })),
  );
}

/**
 * Queue a worktree-backed row's worktree for removal. The row hides like any
 * delete and comes back as a plain branch once `git.changed` reloads the list.
 */
export function enqueueWorktreeRemoval(state: GitDeleteState, target: GitItem): GitDeleteState {
  const path = target.worktree_path ?? '';
  if (!canRemoveWorktree(target) || path === '') return state;
  return enqueueEntries(state, [{ id: gitItemId(target), item: toWorktreeRemoval(path) }]);
}

/** Take the batch for sending, leaving the rows hidden until results land. */
export function takePending(state: GitDeleteState): {
  state: GitDeleteState;
  batch: PendingDelete[];
} {
  return { state: { ...state, pending: [] }, batch: [...state.pending] };
}

/** Undo: un-hide everything queued and drop it. Nothing was sent. */
export function undo(state: GitDeleteState): GitDeleteState {
  const hidden = new Set(state.hidden);
  for (const entry of state.pending) hidden.delete(entry.id);
  return { ...state, hidden, pending: [] };
}

/** Restore rows after a failure — the batch never made it through. */
export function restore(
  state: GitDeleteState,
  batch: readonly PendingDelete[],
): GitDeleteState {
  const hidden = new Set(state.hidden);
  for (const entry of batch) hidden.delete(entry.id);
  return { ...state, hidden };
}

export interface ResultOutcome {
  state: GitDeleteState;
  /** Rows that failed for a reason the user cannot retry away. */
  failures: GitDeleteResult[];
}

/**
 * Apply a batch's results.
 *
 * A `force_required` row comes **back into view** while the prompt names it —
 * nothing was deleted, so leaving it hidden would misreport the tree. Confirming
 * re-hides it. Any other failure restores the row and is reported.
 */
export function applyResults(
  state: GitDeleteState,
  batch: readonly PendingDelete[],
  results: readonly GitDeleteResult[],
): ResultOutcome {
  const unmerged: PendingDelete[] = [];
  const failures: GitDeleteResult[] = [];
  const restoreIds = new Set<string>();

  results.forEach((result, index) => {
    const pending = batch[index];
    // Extra results are ignored: a longer array than the request cannot be
    // mapped onto rows, and guessing would hide the wrong ones.
    if (pending === undefined) return;
    if (result.error === 'force_required') {
      unmerged.push(pending);
      restoreIds.add(pending.id);
    } else if (!result.ok) {
      failures.push(result);
      restoreIds.add(pending.id);
    }
  });

  // A short `results` array leaves those items unreported — treat them as
  // un-deleted so their rows come back rather than vanishing on a server bug.
  if (results.length < batch.length) {
    for (const entry of batch.slice(results.length)) restoreIds.add(entry.id);
  }

  const hidden = new Set(state.hidden);
  for (const id of restoreIds) hidden.delete(id);

  const force: ForcePrompt | null =
    unmerged.length === 0
      ? null
      : {
          items: unmerged,
          refs: unmerged
            .map((entry) => entry.item.branch ?? entry.item.worktree_path ?? '')
            .filter((ref) => ref !== ''),
        };

  return { state: { ...state, hidden, force }, failures };
}

/**
 * Confirm a force delete: re-hide the rows and hand back the same items with
 * `force: true`. This goes through the ordinary send path, so a second
 * `force_required` prompts again.
 */
export function confirmForce(state: GitDeleteState): {
  state: GitDeleteState;
  batch: PendingDelete[];
} {
  const prompt = state.force;
  if (prompt === null) return { state, batch: [] };
  const batch = prompt.items.map((entry) => ({
    id: entry.id,
    item: { ...entry.item, force: true },
  }));
  const hidden = new Set(state.hidden);
  for (const entry of batch) hidden.add(entry.id);
  return { state: { ...state, hidden, force: null }, batch };
}

export function dismissForce(state: GitDeleteState): GitDeleteState {
  return { ...state, force: null };
}

/**
 * Reconcile against a fresh list.
 *
 * Rows the server already dropped stay dropped; anything still listed and no
 * longer queued comes back into view. A pending entry the server no longer lists
 * is dropped from the batch — someone else deleted it first.
 */
export function reconcile(
  state: GitDeleteState,
  items: readonly GitItem[],
): GitDeleteState {
  const surviving = new Set(items.map(gitItemId));
  const hidden = new Set([...state.hidden].filter((id) => surviving.has(id)));
  const pending = state.pending.filter((entry) => surviving.has(entry.id));
  // Identity is preserved when nothing changed, so the caller can run this on
  // every refetch from an effect without churning state on each one.
  if (hidden.size === state.hidden.size && pending.length === state.pending.length) {
    return state;
  }
  return { ...state, hidden, pending };
}

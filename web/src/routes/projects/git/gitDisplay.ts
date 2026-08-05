/**
 * Row derivations for the git list — the port of `GitItemDisplay` (iOS) and
 * `GitItemRow`'s chip helpers (Android), which agree line for line.
 */
import {
  KIND_BRANCH_WITH_WORKTREE,
  KIND_ORPHAN_WORKTREE,
  type GitDeleteResult,
  type GitItem,
} from '../../../api/gitRepository';

export type ChipStyle = 'highlight' | 'danger' | 'neutral' | 'muted';

export interface Chip {
  text: string;
  style: ChipStyle;
}

export function primaryLabel(item: GitItem): string {
  return item.kind === KIND_ORPHAN_WORKTREE
    ? (item.worktree_path ?? '')
    : (item.branch ?? '');
}

export function secondaryLabel(item: GitItem): string | null {
  if (item.kind === KIND_BRANCH_WITH_WORKTREE) return item.worktree_path ?? null;
  if (item.kind === KIND_ORPHAN_WORKTREE) return item.detached ? '(detached HEAD)' : null;
  return null;
}

/** Fixed order; `upstream` is deliberately not rendered, as on both mobile clients. */
export function chips(item: GitItem): Chip[] {
  const result: Chip[] = [];
  if (item.is_current) result.push({ text: 'current', style: 'highlight' });
  if (item.gone) result.push({ text: 'gone', style: 'danger' });
  if (item.ahead > 0 || item.behind > 0) {
    result.push({
      text: `ahead ${String(item.ahead)} / behind ${String(item.behind)}`,
      style: 'neutral',
    });
  }
  if (item.locked) {
    const reason = item.locked_reason?.trim() ?? '';
    result.push({ text: reason === '' ? 'locked' : `locked: ${reason}`, style: 'danger' });
  }
  if (item.kind === KIND_ORPHAN_WORKTREE) result.push({ text: 'orphan', style: 'danger' });
  if (item.prunable) result.push({ text: 'prunable', style: 'muted' });
  return result;
}

export function deleteFailureMessage(failures: readonly GitDeleteResult[]): string {
  const only = failures.length === 1 ? failures[0] : undefined;
  if (only === undefined) return `${String(failures.length)} item(s) failed to delete`;
  return `Couldn't delete ${only.target}: ${deleteFailureReason(only)}`;
}

/**
 * `error_detail` is raw git stderr — first line, capped, so a one-line toast
 * cannot turn into a wall of text.
 */
export function deleteFailureReason(failure: GitDeleteResult): string {
  const detail = failure.error_detail?.split('\n')[0]?.trim() ?? '';
  if (detail !== '') return detail.slice(0, 120);
  switch (failure.error) {
    case 'locked':
      return 'the worktree is locked';
    case 'not_found':
      return 'it no longer exists';
    case 'invalid':
      return 'the request was invalid';
    default:
      return 'unknown error';
  }
}

export function forceDeleteMessage(refs: readonly string[]): string {
  return (
    "These items have unmerged commits or uncommitted changes and can't be deleted " +
    `normally: ${refs.join(', ')}. Force-delete permanently discards that work.`
  );
}

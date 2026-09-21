/**
 * Row derivations for the git list — the port of `GitItemDisplay` (iOS) and
 * `GitItemRow`'s chip helpers (Android), which agree line for line.
 */
import {
  GIT_CODE_BAD_BRANCH_NAME,
  GIT_CODE_CHECKED_OUT_ELSEWHERE,
  GIT_CODE_DIRTY,
  GIT_CODE_EXISTS,
  GIT_CODE_OPERATION_IN_PROGRESS,
  GIT_CODE_REF_NOT_FOUND,
  GIT_CODE_UNKNOWN_START_POINT,
  KIND_BRANCH_WITH_WORKTREE,
  KIND_ORPHAN_WORKTREE,
  KIND_REMOTE_BRANCH,
  compareRefNames,
  type GitBranchResult,
  type GitDeleteResult,
  type GitItem,
} from '../../../api/gitRepository';
import {
  GIT_CODE_JOB_RUNNING,
  GIT_CODE_TIMEOUT,
  GIT_JOB_CODE_AUTH_REQUIRED,
  isSessionGitError,
} from '../../../api/sessionGitRepository';
import { jobErrorCopy, sessionGitErrorCopy } from '../../terminal/git/sessionGitDisplay';

/** Below this many rows a filter field would take more space than it saves. */
export const FILTER_THRESHOLD = 8;

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

/** The tip commit's subject and day, as the agent reported them. */
export function tipLine(item: GitItem): string | null {
  const subject = item.subject?.trim() ?? '';
  const date = shortDate(item.committed_at);
  if (subject === '' && date === null) return null;
  if (subject === '') return date;
  if (date === null) return subject;
  return `${subject} · ${date}`;
}

/**
 * `YYYY-MM-DD` from the agent's `iso-strict` committer date — a prefix, not a
 * parse, so the day stays the committer's own on all three clients.
 */
export function shortDate(iso: string | null | undefined): string | null {
  if (iso === null || iso === undefined || iso === '') return null;
  return iso.length > 10 ? iso.slice(0, 10) : iso;
}

/** A case-insensitive substring match on the branch and the worktree path. */
export function matchesFilter(item: GitItem, filter: string): boolean {
  const needle = filter.trim().toLowerCase();
  if (needle === '') return true;
  return (
    primaryLabel(item).toLowerCase().includes(needle) ||
    (item.worktree_path ?? '').toLowerCase().includes(needle)
  );
}

export type GitTreeScope = 'local' | 'remote';

export interface GitFolderRow {
  type: 'folder';
  id: string;
  name: string;
  depth: number;
  /** The rows below it that the current filter shows. */
  count: number;
  expanded: boolean;
}

export interface GitLeafRow {
  type: 'item';
  item: GitItem;
  depth: number;
  /** The last path segment; dialogs and accessible names keep `primaryLabel`. */
  label: string;
}

export type GitTreeRow = GitFolderRow | GitLeafRow;

interface TreeNode {
  segments: string[];
  folders: Map<string, TreeNode>;
  leaves: GitLeafRow[];
}

/**
 * Where a row sits in the tree: its branch split on `/`, a remote-tracking row
 * under a top folder named by its `remote` (which may itself contain `/`).
 */
export function treeSegments(item: GitItem): string[] {
  const branch = item.branch ?? '';
  const remote = item.remote ?? '';
  if (item.kind === KIND_REMOTE_BRANCH && remote !== '' && branch.startsWith(`${remote}/`)) {
    return [remote, ...splitPath(branch.slice(remote.length + 1))];
  }
  return splitPath(branch);
}

function splitPath(name: string): string[] {
  return name.split('/').filter((segment) => segment !== '');
}

/**
 * `:` is illegal in ref and remote names, so it separates a remote from the
 * path below it: remotes `a` and `a/b` never share a folder id, and no folder
 * id can equal a row id (`<kind>:…`).
 */
export function folderId(scope: GitTreeScope, segments: readonly string[]): string {
  if (scope === 'remote') {
    return `dir:remote:${segments[0] ?? ''}:${segments.slice(1).join('/')}`;
  }
  return `dir:local:${segments.join('/')}`;
}

/** Remote-name folders start open — the collapsed section is that level already. */
function expandedByDefault(scope: GitTreeScope, depth: number): boolean {
  return scope === 'remote' && depth === 0;
}

/**
 * The visible rows of one section as a flattened tree: at each level folders
 * A→Z, then rows A→Z. `toggled` holds the folders the user flipped away from
 * their default; an active filter opens every folder (the rows passed in are
 * already filtered, so a folder without a match never appears). Orphan
 * worktrees have no branch to file them under and follow the local tree by path.
 */
export function gitTreeRows(
  items: readonly GitItem[],
  scope: GitTreeScope,
  toggled: ReadonlySet<string>,
  filterActive: boolean,
): GitTreeRow[] {
  const root: TreeNode = { segments: [], folders: new Map(), leaves: [] };
  const orphans: GitItem[] = [];
  for (const item of items) {
    if (item.kind === KIND_ORPHAN_WORKTREE) {
      orphans.push(item);
      continue;
    }
    const segments = treeSegments(item);
    let node = root;
    for (const segment of segments.slice(0, -1)) {
      let child = node.folders.get(segment);
      if (child === undefined) {
        child = { segments: [...node.segments, segment], folders: new Map(), leaves: [] };
        node.folders.set(segment, child);
      }
      node = child;
    }
    node.leaves.push({
      type: 'item',
      item,
      depth: node.segments.length,
      label: segments.at(-1) ?? primaryLabel(item),
    });
  }

  const rows: GitTreeRow[] = [];
  const visit = (node: TreeNode): void => {
    const folders = [...node.folders.entries()].sort(([a], [b]) => compareRefNames(a, b));
    for (const [name, folder] of folders) {
      const depth = node.segments.length;
      const id = folderId(scope, folder.segments);
      const expanded = filterActive || expandedByDefault(scope, depth) !== toggled.has(id);
      rows.push({ type: 'folder', id, name, depth, count: leafCount(folder), expanded });
      if (expanded) visit(folder);
    }
    const leaves = [...node.leaves].sort((a, b) => compareRefNames(a.label, b.label));
    rows.push(...leaves);
  };
  visit(root);

  orphans
    .sort((a, b) => compareRefNames(primaryLabel(a), primaryLabel(b)))
    .forEach((item) => rows.push({ type: 'item', item, depth: 0, label: primaryLabel(item) }));
  return rows;
}

function leafCount(node: TreeNode): number {
  let count = node.leaves.length;
  for (const child of node.folders.values()) count += leafCount(child);
  return count;
}

/** Fixed order; `upstream` is deliberately not rendered, as on both mobile clients. */
export function chips(item: GitItem): Chip[] {
  const result: Chip[] = [];
  if (item.is_current) result.push({ text: 'current', style: 'highlight' });
  if (item.kind === KIND_REMOTE_BRANCH) {
    result.push({ text: item.remote ?? 'remote', style: 'muted' });
  }
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
  // A remote delete's push failures share the fetch/pull/push vocabulary; git's
  // own wording for a disabled prompt says nothing about what to fix.
  if (failure.error === GIT_JOB_CODE_AUTH_REQUIRED) return jobErrorCopy(failure.error, null);
  const detail = failure.error_detail?.split('\n')[0]?.trim() ?? '';
  if (detail !== '') return detail.slice(0, 120);
  switch (failure.error) {
    case 'locked':
      return 'the worktree is locked';
    case 'not_found':
      return 'it no longer exists';
    case 'invalid':
      return 'the request was invalid';
    case GIT_CODE_TIMEOUT:
      return jobErrorCopy(failure.error, null);
    default:
      return 'unknown error';
  }
}

export function remoteDeleteMessage(refs: readonly string[]): string {
  const what = refs.length === 1 ? (refs[0] ?? '') : `${String(refs.length)} branches`;
  return (
    `Delete ${what} on the remote? This removes ${refs.length === 1 ? 'it' : 'them'} ` +
    'for everyone with access to the remote.'
  );
}

export function forceDeleteMessage(refs: readonly string[]): string {
  return (
    "These items have unmerged commits or uncommitted changes and can't be deleted " +
    `normally: ${refs.join(', ')}. Force-delete permanently discards that work.`
  );
}

export function checkoutConfirmMessage(branch: string, projectName: string): string {
  return (
    `Check out ${branch} in the project folder of ${projectName}? ` +
    'Sessions started in that folder afterwards use this branch.'
  );
}

export function checkedOutMessage(result: GitBranchResult): string {
  return `Checked out ${result.branch}`;
}

export function branchCreatedMessage(result: GitBranchResult): string {
  return result.checked_out
    ? `Created and checked out ${result.branch}`
    : `Created ${result.branch}`;
}

/** The checkout refusals, word for word what Android and iOS show. */
export function checkoutErrorCopy(error: unknown, branch: string): string {
  if (isSessionGitError(error) && error.kind === 'git') {
    switch (error.code) {
      case GIT_CODE_DIRTY:
        return (
          'The project folder has local changes this checkout would overwrite. ' +
          "Commit or discard them from a session's Changes screen first."
        );
      case GIT_CODE_CHECKED_OUT_ELSEWHERE: {
        const where = error.detailMessage;
        return where === null || where === ''
          ? `${branch} is already checked out in another worktree.`
          : `${branch} is already checked out in another worktree (${where}).`;
      }
      case GIT_CODE_OPERATION_IN_PROGRESS:
        return (
          'A merge, rebase or cherry-pick is in progress in the project folder. ' +
          'Finish or abort it first.'
        );
      case GIT_CODE_JOB_RUNNING:
        return 'A fetch, pull or push is still running in the project folder.';
      case GIT_CODE_REF_NOT_FOUND:
        return 'Branch not found — refresh the list.';
      default:
        break;
    }
  }
  return sessionGitErrorCopy(error);
}

/** The create refusals shown inline in the New branch dialog. */
export function createBranchErrorCopy(error: unknown, name: string): string {
  if (isSessionGitError(error) && error.kind === 'git') {
    switch (error.code) {
      case GIT_CODE_EXISTS:
        return `A branch named ${name} already exists.`;
      case GIT_CODE_BAD_BRANCH_NAME:
        return 'Not a valid branch name.';
      case GIT_CODE_UNKNOWN_START_POINT: {
        const start = error.detailMessage;
        return start === null || start === ''
          ? 'The start point was not found.'
          : `Start point ${start} was not found.`;
      }
      default:
        break;
    }
  }
  return checkoutErrorCopy(error, name);
}

/**
 * Row and copy derivations for the in-session Changes viewer — the port of
 * Android `GitChangesUiState.kt` / `GitDiffUiState.kt` and iOS
 * `GitChangesModel` / `GitDiffModel` / `SessionGitCopy`, which agree line for
 * line. Pure, so the three clients' strings can be diffed against each other.
 */
import { isApiError } from '../../../api/errors';
import {
  KIND_BRANCH_ONLY,
  KIND_BRANCH_WITH_WORKTREE,
  KIND_REMOTE_BRANCH,
  compareRefNames,
  type GitItem,
} from '../../../api/gitRepository';
import {
  GIT_CODE_CONFLICT_MARKERS,
  GIT_CODE_EMPTY_MESSAGE,
  GIT_CODE_ERROR,
  GIT_CODE_HOOK_FAILED,
  GIT_CODE_IDENTITY_REQUIRED,
  GIT_CODE_INDEX_LOCKED,
  GIT_CODE_JOB_NOT_FOUND,
  GIT_CODE_JOB_RUNNING,
  GIT_CODE_NO_HEAD,
  GIT_CODE_NO_REBASE,
  GIT_CODE_NOTHING_TO_COMMIT,
  GIT_CODE_PATHSPEC_MISMATCH,
  GIT_CODE_TIMEOUT,
  GIT_CODE_TOO_OLD,
  GIT_CODE_UNAVAILABLE,
  GIT_CODE_UNKNOWN_REF,
  GIT_CODE_UNMERGED,
  GIT_JOB_CODE_AUTH_REQUIRED,
  GIT_JOB_CODE_CANCELLED,
  GIT_JOB_CODE_CONFLICTS,
  GIT_JOB_CODE_DETACHED_HEAD,
  GIT_JOB_CODE_DIRTY_WORKTREE,
  GIT_JOB_CODE_NETWORK,
  GIT_JOB_CODE_NO_REMOTE,
  GIT_JOB_CODE_NO_UPSTREAM,
  GIT_JOB_CODE_NOT_FAST_FORWARD,
  GIT_JOB_CODE_OPERATION_IN_PROGRESS,
  GIT_JOB_CODE_REJECTED,
  GIT_JOB_CODE_REMOTE_NOT_FOUND,
  GIT_REBASE_ABORTED,
  GIT_REBASE_REBASED,
  GIT_REBASE_STOPPED,
  GIT_REBASE_UP_TO_DATE,
  isSessionGitError,
  type SessionGitCommitHead,
  type SessionGitCommitResult,
  type SessionGitDiff,
  type SessionGitDiffScope,
  type SessionGitDiscardScope,
  type SessionGitEntry,
  type SessionGitHunk,
  type SessionGitJob,
  type SessionGitJobOp,
  type SessionGitRebaseResult,
  type SessionGitRepoInfo,
  type SessionGitStatus,
  type SessionGitSyncRequest,
  type SessionGitUpstream,
} from '../../../api/sessionGitRepository';
import type { GitCapabilities } from '../../../protocol/dto/agentVersion';

export const DIFF_CONTEXT_DEFAULT = 3;
/** The agent's context ceiling, at which an untruncated diff is the whole file. */
export const DIFF_CONTEXT_FULL = 5000;
export const DIFF_CONTEXT_CHOICES: readonly { context: number; label: string }[] = [
  { context: DIFF_CONTEXT_DEFAULT, label: 'Changes' },
  { context: DIFF_CONTEXT_FULL, label: 'Full file' },
];

const CONTEXTLESS_CHANGES: ReadonlySet<string> = new Set(['added', 'deleted', 'type_changed']);

/**
 * Whether the context choice can change what is shown: only a text diff with
 * hunks of a file present on both sides has unchanged lines to widen. An added
 * or deleted file is one all-add or all-del hunk, and a type change is both.
 */
export function showsContextChoices(diff: SessionGitDiff): boolean {
  if (diff.kind !== 'text' || (diff.hunks ?? []).length === 0) return false;
  const change = diff.change ?? null;
  return change === null || !CONTEXTLESS_CHANGES.has(change);
}

/** Wire groups in display order; the wire's `unstaged` reads as "Changes". */
const SECTION_ORDER: readonly { group: string; title: string }[] = [
  { group: 'conflicts', title: 'Conflicts' },
  { group: 'staged', title: 'Staged' },
  { group: 'unstaged', title: 'Changes' },
  { group: 'untracked', title: 'Untracked' },
];

export interface ChangesRow {
  /**
   * `group:occurrence:path`, occurrence first: unique even when the agent's
   * lossy decoding of two POSIX filenames yields the same `path`, and no
   * filename can forge another row's key. The occurrence counts rows of the
   * same group *and* path — not the row's index in its section, which would
   * rename every later row when a file appears above it and cost a remount
   * (and the selection) on each poll.
   */
  key: string;
  entry: SessionGitEntry;
}

export interface ChangesSection {
  group: string;
  title: string;
  rows: ChangesRow[];
}

/** Known groups in fixed order, then any group a newer agent adds, sorted. */
export function sectionsFor(status: SessionGitStatus): ChangesSection[] {
  const byGroup = new Map<string, ChangesRow[]>();
  const seen = new Map<string, number>();
  for (const entry of status.entries ?? []) {
    const identity = `${entry.group} ${entry.path}`;
    const occurrence = seen.get(identity) ?? 0;
    seen.set(identity, occurrence + 1);
    const rows = byGroup.get(entry.group) ?? [];
    rows.push({ key: `${entry.group}:${String(occurrence)}:${entry.path}`, entry });
    byGroup.set(entry.group, rows);
  }
  const known = SECTION_ORDER.flatMap(({ group, title }) => {
    const rows = byGroup.get(group);
    return rows === undefined ? [] : [{ group, title, rows }];
  });
  const other = [...byGroup.keys()]
    .filter((group) => !SECTION_ORDER.some((section) => section.group === group))
    .sort()
    .map((group) => ({ group, title: group.replace(/_/g, ' '), rows: byGroup.get(group) ?? [] }));
  return [...known, ...other];
}

/** Conflicted files are diffed against HEAD, which the agent serves as `unstaged`. */
export function diffScopeFor(entry: SessionGitEntry): SessionGitDiffScope {
  if (entry.group === 'staged') return 'staged';
  if (entry.group === 'untracked') return 'untracked';
  return 'unstaged';
}

export type BadgeTone = 'added' | 'deleted' | 'modified' | 'renamed' | 'conflict' | 'untracked';

export interface StatusBadge {
  label: string;
  tone: BadgeTone;
}

export function badgeFor(entry: SessionGitEntry): StatusBadge {
  if (entry.group === 'conflicts') {
    return { label: entry.code === '' ? 'U' : entry.code, tone: 'conflict' };
  }
  if (entry.group === 'untracked') return { label: '?', tone: 'untracked' };
  switch (entry.code) {
    case 'A':
      return { label: 'A', tone: 'added' };
    case 'D':
      return { label: 'D', tone: 'deleted' };
    case 'R':
      return { label: 'R', tone: 'renamed' };
    case 'C':
      return { label: 'C', tone: 'renamed' };
    default:
      return { label: entry.code === '' ? 'M' : entry.code, tone: 'modified' };
  }
}

/**
 * The last path segment. Git lists an untracked nested repository or worktree
 * as `dir/`, so a trailing slash stays on the name instead of leaving it empty.
 */
export function fileName(path: string): string {
  return path.slice(withoutTrailingSlash(path).lastIndexOf('/') + 1);
}

export function directory(path: string): string | null {
  const trimmed = withoutTrailingSlash(path);
  const cut = trimmed.lastIndexOf('/');
  return cut <= 0 ? null : trimmed.slice(0, cut);
}

function withoutTrailingSlash(path: string): string {
  return path.endsWith('/') ? path.slice(0, -1) : path;
}

export function headLabel(repo: SessionGitRepoInfo): string {
  const head = repo.head;
  if (head.initial) return 'No commits yet';
  if (head.detached) return `detached @ ${head.short_oid ?? 'HEAD'}`;
  return head.branch ?? head.short_oid ?? 'HEAD';
}

export function upstreamLabel(upstream: SessionGitUpstream | null | undefined): string | null {
  if (upstream === null || upstream === undefined) return null;
  const { ahead, behind } = upstream;
  if (ahead === null || ahead === undefined || behind === null || behind === undefined) {
    return `${upstream.ref} · upstream gone`;
  }
  return `↑${String(ahead)} ↓${String(behind)} · ${upstream.ref}`;
}

export function repoStateLabel(state: string): string | null {
  if (state === 'clean') return null;
  if (state === 'cherry_picking') return 'cherry-picking';
  return state.replace(/_/g, ' ');
}

const UNAVAILABLE_REASONS = new Set([
  'not_a_repo',
  'bare_repository',
  'cwd_missing',
  'unsafe_directory',
  'git_too_old',
]);

export function unavailableCopy(reason: string): { title: string; body: string } {
  switch (reason) {
    case 'not_a_repo':
      return {
        title: 'Not a git repository',
        body: "The session's folder is not inside a git repository.",
      };
    case 'bare_repository':
      return {
        title: 'Bare repository',
        body: 'The session runs inside a bare repository, which has no working tree to show.',
      };
    case 'cwd_missing':
      return {
        title: 'Folder not found',
        body: "The session's folder no longer exists on the agent host.",
      };
    case 'unsafe_directory':
      return {
        title: 'Unsafe directory',
        body:
          'Git refuses this folder because another user owns it. ' +
          'Allow it with `git config --global --add safe.directory <path>` on the agent host.',
      };
    case 'git_too_old':
      return { title: 'Git is too old', body: 'The agent host needs git 2.15 or newer.' };
    default:
      return {
        title: 'Changes unavailable',
        body: `The agent could not read this repository (${reason}).`,
      };
  }
}

export const NETWORK_FAILURE_MESSAGE =
  'Could not reach the agent. Check the connection and try again.';

export function sessionGitErrorCopy(error: unknown): string {
  if (isSessionGitError(error)) {
    switch (error.kind) {
      case 'agentOutdated':
        return 'Update your agent to view changes.';
      case 'sessionGone':
        return 'This session has ended.';
      case 'git': {
        const code = error.code ?? '';
        if (code === GIT_CODE_UNAVAILABLE) return 'git is not installed on the agent host.';
        if (code === GIT_CODE_TIMEOUT) return 'git took too long to answer. Try again.';
        if (code === GIT_CODE_ERROR) {
          return error.detailMessage === null
            ? 'git failed unexpectedly.'
            : `git failed: ${error.detailMessage}`;
        }
        if (UNAVAILABLE_REASONS.has(code)) return unavailableCopy(code).body;
        return refusalCopy(code, error.detailMessage);
      }
    }
  }
  // `toApiError` has already folded every `detail` shape into the message.
  if (isApiError(error)) return error.message;
  // openapi-fetch rethrows what `fetch` threw, and a refused connection is a
  // `TypeError`; an aborted request never reaches a screen.
  if (error instanceof TypeError) return NETWORK_FAILURE_MESSAGE;
  if (error instanceof Error && error.message !== '') return error.message;
  return 'Something went wrong.';
}

/**
 * The list's cut-short notice. Counts come from the full parse, but when the
 * agent's byte cap hit before the entry cap they are a lower bound too — so the
 * shown figure is what actually arrived and the total is never claimed exact.
 */
export function truncatedEntriesNotice(status: SessionGitStatus): string | null {
  if (!status.truncated) return null;
  const shown = status.entries?.length ?? 0;
  const total = status.counts?.total;
  if (total !== undefined && total > shown) {
    return `Showing the first ${String(shown)} of at least ${String(total)} entries.`;
  }
  return `List cut short after ${String(shown)} entries.`;
}

export function changeLabel(change: string): string {
  return change.replace(/_/g, ' ');
}

const TAB_WIDTH = 4;

/**
 * Tabs become spaces, because a tab stop is measured from the row's edge and
 * the gutters sit before the text; a carriage return becomes a visible ␍ so a
 * CRLF file reads as one rather than hiding a control character.
 */
export function renderableText(text: string): string {
  if (!text.includes('\t') && !text.includes('\r')) return text;
  return text.replace(/\t/g, ' '.repeat(TAB_WIDTH)).replace(/\r/g, '␍');
}

export function hunkHeader(hunk: SessionGitHunk): string {
  const range =
    `@@ -${String(hunk.old_start)},${String(hunk.old_lines)} ` +
    `+${String(hunk.new_start)},${String(hunk.new_lines)} @@`;
  return hunk.heading.trim() === '' ? range : `${range}  ${hunk.heading}`;
}

export interface DiffLineRow {
  key: string;
  t: 'ctx' | 'add' | 'del';
  old: number | null;
  new: number | null;
  text: string;
  noNewline: boolean;
}

export interface DiffHunkView {
  key: string;
  header: string;
  lines: DiffLineRow[];
}

export interface DiffView {
  hunks: DiffHunkView[];
  /** Replaces the hunks: binary / too large / missing / empty text diff. */
  notice: string | null;
  /** Follows the hunks of a truncated diff. */
  truncated: string | null;
  /** Digits in the widest line number, so both gutters share one width. */
  gutterDigits: number;
}

export function diffView(diff: SessionGitDiff): DiffView {
  const empty: DiffView = { hunks: [], notice: null, truncated: null, gutterDigits: 2 };
  switch (diff.kind) {
    case 'binary':
      return { ...empty, notice: 'Binary file — no preview' };
    case 'too_large':
      return {
        ...empty,
        notice: `File too large to preview (${String(Math.round(diff.byte_limit / (1024 * 1024)))} MiB limit)`,
      };
    case 'missing':
      return { ...empty, notice: 'Nothing to show for this file' };
    default:
      break;
  }
  let shown = 0;
  const hunks: DiffHunkView[] = [];
  (diff.hunks ?? []).forEach((hunk, h) => {
    const lines = hunk.lines ?? [];
    // A hunk with no lines in a truncated diff is overflow past the cap, not content.
    if (diff.truncated && lines.length === 0) return;
    hunks.push({
      key: `h${String(h)}`,
      header: hunkHeader(hunk),
      lines: lines.map((line, i) => {
        shown += 1;
        return {
          key: `h${String(h)}:l${String(i)}`,
          t: line.t,
          old: line.old ?? null,
          new: line.new ?? null,
          text: renderableText(line.text),
          noNewline: line.no_newline,
        };
      }),
    });
  });
  const truncated = diff.truncated ? truncatedDiffNotice(shown, diff.total_lines) : null;
  const notice = !diff.truncated && hunks.length === 0 ? emptyDiffNotice(diff) : null;
  return { hunks, notice, truncated, gutterDigits: gutterDigits(diff) };
}

/**
 * `total_lines` is only a lower bound when the agent's byte cap cut git off
 * (PROTOCOL.md), so the copy never claims an exact size.
 */
export function truncatedDiffNotice(shown: number, total: number): string {
  return total > shown
    ? `Showing the first ${String(shown)} of at least ${String(total)} lines`
    : `Truncated after ${String(shown)} lines — the full diff is longer`;
}

export function emptyDiffNotice(diff: SessionGitDiff): string {
  const oldMode = diff.old_mode;
  const newMode = diff.new_mode;
  if (
    oldMode !== null &&
    oldMode !== undefined &&
    newMode !== null &&
    newMode !== undefined &&
    oldMode !== newMode
  ) {
    return `Mode changed only: ${oldMode} → ${newMode}`;
  }
  return 'No text changes';
}

function gutterDigits(diff: SessionGitDiff): number {
  let widest = 0;
  for (const hunk of diff.hunks ?? []) {
    widest = Math.max(widest, hunk.old_start + hunk.old_lines, hunk.new_start + hunk.new_lines);
  }
  return Math.max(2, String(widest).length);
}

/**
 * The mutating routes' `409` vocabulary, one line each; the same strings as
 * Android `refusalCopy` and iOS `SessionGitCopy.refusal`.
 */
export function refusalCopy(code: string, detailMessage: string | null): string {
  switch (code) {
    case GIT_CODE_IDENTITY_REQUIRED:
      return "Git doesn't know who you are on the agent machine.";
    case GIT_CODE_UNMERGED:
      return 'Resolve the conflicts first.';
    case GIT_CODE_NOTHING_TO_COMMIT:
      return 'Nothing is staged.';
    case GIT_CODE_NO_HEAD:
      return 'There is no commit to amend yet.';
    case GIT_CODE_HOOK_FAILED:
      return 'A commit hook rejected the commit.';
    case GIT_CODE_INDEX_LOCKED:
      return 'Another git process is running — try again.';
    case GIT_CODE_PATHSPEC_MISMATCH:
      return 'That file changed under you — refreshed.';
    case GIT_CODE_EMPTY_MESSAGE:
      return 'Enter a commit summary.';
    case GIT_CODE_JOB_RUNNING:
      return 'Another fetch, pull or push is still running.';
    case GIT_CODE_JOB_NOT_FOUND:
      return JOB_LOST_COPY;
    case GIT_CODE_CONFLICT_MARKERS:
      return `${detailMessage ?? 'The file'} still has conflict markers — finish resolving it first.`;
    default:
      return detailMessage === null
        ? `git refused the request (${code}).`
        : `git refused the request (${code}): ${detailMessage}`;
  }
}

/**
 * Copy for a failed stage / unstage / discard / commit. One code reads
 * differently from the polls: the mutating routes need git 2.26, the reads
 * 2.15, and the agent answers `git_too_old` for both.
 */
export function mutationErrorCopy(error: unknown): string {
  if (isSessionGitError(error) && error.kind === 'git' && error.code === GIT_CODE_TOO_OLD) {
    return 'Staging needs git 2.26 or newer on the agent machine.';
  }
  return sessionGitErrorCopy(error);
}

/** The host-side fix shown with `identity_required`, verbatim for copying. */
export const IDENTITY_FIX_COMMANDS: readonly string[] = [
  'git config --global user.name "Your Name"',
  'git config --global user.email "you@example.com"',
];

export type RowAction = 'stage' | 'unstage' | 'discard' | 'resolve';

export interface RowActions {
  stage: boolean;
  unstage: boolean;
  discard: boolean;
  /** Stage a conflicted file once it is fixed; the agent refuses one that still holds markers. */
  resolve: boolean;
}

const NO_ACTIONS: RowActions = { stage: false, unstage: false, discard: false, resolve: false };

/**
 * What a row can do: a staged entry is unstaged, a modified or untracked one
 * is staged or discarded, a conflict is marked resolved once fixed — only on
 * an agent that refuses to stage leftover markers (`resolve`). Each gated on
 * its capability, so an older agent shows no control that would 404.
 */
export function rowActions(entry: SessionGitEntry, capabilities: GitCapabilities): RowActions {
  switch (entry.group) {
    case 'staged':
      return { ...NO_ACTIONS, unstage: capabilities.stage };
    case 'unstaged':
    case 'untracked':
      return { ...NO_ACTIONS, stage: capabilities.stage, discard: capabilities.discard };
    case 'conflicts':
      return { ...NO_ACTIONS, resolve: capabilities.stage && capabilities.resolve };
    default:
      return NO_ACTIONS;
  }
}

/** The discard route's scope for a row; conflicts never reach it. */
export function discardScopeFor(entry: SessionGitEntry): SessionGitDiscardScope {
  return entry.group === 'untracked' ? 'untracked' : 'unstaged';
}

/**
 * The paths a request names for these rows: a rename needs both its sides
 * (`git reset` on the new name alone leaves the deletion staged).
 */
export function pathsFor(rows: readonly ChangesRow[]): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const { entry } of rows) {
    for (const path of [entry.path, entry.orig_path ?? null]) {
      if (path === null || seen.has(path)) continue;
      seen.add(path);
      out.push(path);
    }
  }
  return out;
}

export interface SelectionSubsets {
  stage: ChangesRow[];
  unstage: ChangesRow[];
  discard: ChangesRow[];
}

/** The selected rows each toolbar action applies to; a button is enabled when its subset is non-empty. */
export function selectionSubsets(
  sections: readonly ChangesSection[],
  selectedKeys: ReadonlySet<string>,
  capabilities: GitCapabilities,
): SelectionSubsets {
  const subsets: SelectionSubsets = { stage: [], unstage: [], discard: [] };
  for (const section of sections) {
    for (const row of section.rows) {
      if (!selectedKeys.has(row.key)) continue;
      const actions = rowActions(row.entry, capabilities);
      if (actions.stage) subsets.stage.push(row);
      if (actions.unstage) subsets.unstage.push(row);
      if (actions.discard) subsets.discard.push(row);
    }
  }
  return subsets;
}

/** The whole-section action of a header, or none for Conflicts and unknown groups. */
export function sectionAction(
  section: ChangesSection,
  capabilities: GitCapabilities,
): { label: string; action: 'stage' | 'unstage' } | null {
  if (!capabilities.stage) return null;
  if (section.group === 'staged') return { label: 'Unstage all', action: 'unstage' };
  if (section.group === 'unstaged' || section.group === 'untracked') {
    return { label: 'Stage all', action: 'stage' };
  }
  return null;
}

export function discardPrompt(rows: readonly ChangesRow[]): { title: string; body: string } {
  const only = rows.length === 1 ? rows[0] : undefined;
  if (only !== undefined) {
    const entry = only.entry;
    const untracked = entry.group === 'untracked';
    return {
      title: untracked ? 'Delete untracked file?' : 'Discard changes?',
      body: untracked
        ? `${entry.path} is not tracked by git and will be deleted. This cannot be undone.`
        : `Changes to ${entry.path} will be thrown away. This cannot be undone.`,
    };
  }
  return {
    title: `Discard ${String(rows.length)} files?`,
    body:
      'Their working-tree changes will be thrown away and untracked ones deleted. ' +
      'This cannot be undone.',
  };
}

export function commitNotice(result: SessionGitCommitResult): string {
  return `Committed ${result.short_oid} · ${result.subject}`;
}

export function authorLine(author: { name: string; email: string }): string {
  return `Committing as ${author.name} <${author.email}>`;
}

/** `subject\n\nbody` — the shape `git commit -F` reads. */
export function composeCommitMessage(summary: string, description: string): string {
  const subject = summary.trim();
  const body = description.trim();
  return body === '' ? subject : `${subject}\n\n${body}`;
}

export function splitCommitMessage(head: SessionGitCommitHead): {
  summary: string;
  description: string;
} {
  return { summary: head.subject, description: head.body };
}

export function commitButtonLabel(stagedCount: number, amend: boolean): string {
  if (amend) return 'Amend commit';
  if (stagedCount === 1) return 'Commit 1 file';
  return `Commit ${String(stagedCount)} files`;
}

/** How long a successful job's banner stays before it clears itself. */
export const JOB_RESULT_DISMISS_MS = 6_000;

/** The reconcile poll's `404 job_not_found`, wherever a job is shown. */
export const JOB_LOST_COPY = 'Lost track of the job — refresh to see the result.';

export interface SyncRow {
  op: SessionGitJobOp;
  label: string;
  /** What the row will do, or why it cannot. */
  detail: string;
  enabled: boolean;
}

/**
 * The Sync sheet's three rows, from the same status the header renders; the
 * copy is shared with Android `syncRows` and iOS `GitChangesDisplay.syncRows`.
 * A row exists only when the agent advertises its capability; Fetch is always
 * possible, Pull needs something to pull, Push something to push — or a
 * branch that has no upstream yet, which it publishes.
 */
export function syncRows(
  repo: SessionGitRepoInfo | null | undefined,
  capabilities: GitCapabilities,
): SyncRow[] {
  const rows: SyncRow[] = [];
  const head = repo?.head;
  const upstream = repo?.upstream ?? null;
  const ref = upstream?.ref;
  const blocked =
    head === undefined
      ? 'Repository unavailable'
      : head.initial
        ? 'No commits yet'
        : head.detached
          ? 'Detached HEAD'
          : null;
  if (capabilities.fetch) {
    rows.push({ op: 'fetch', label: 'Fetch', detail: 'Update remote-tracking branches', enabled: true });
  }
  if (capabilities.pull) {
    if (blocked !== null) {
      rows.push({ op: 'pull', label: 'Pull', detail: blocked, enabled: false });
    } else if (upstream === null) {
      rows.push({ op: 'pull', label: 'Pull', detail: 'No upstream', enabled: false });
    } else if (upstream.behind === null || upstream.behind === undefined) {
      rows.push({ op: 'pull', label: 'Pull', detail: 'Upstream gone', enabled: false });
    } else if (upstream.behind === 0) {
      rows.push({ op: 'pull', label: 'Pull', detail: 'Nothing to pull', enabled: false });
    } else {
      rows.push({
        op: 'pull',
        label: 'Pull',
        detail: `Fast-forward ${String(upstream.behind)} ${upstream.behind === 1 ? 'commit' : 'commits'} from ${ref ?? 'upstream'}`,
        enabled: true,
      });
    }
  }
  if (capabilities.push) {
    if (blocked !== null) {
      rows.push({ op: 'push', label: 'Push', detail: blocked, enabled: false });
    } else if (upstream === null) {
      rows.push({
        op: 'push',
        label: 'Publish branch',
        detail: 'Push and set the upstream',
        enabled: true,
      });
    } else if (upstream.ahead === null || upstream.ahead === undefined) {
      rows.push({ op: 'push', label: 'Push', detail: `Recreate ${ref ?? 'the upstream'}`, enabled: true });
    } else if (upstream.ahead === 0) {
      rows.push({ op: 'push', label: 'Push', detail: 'Nothing to push', enabled: false });
    } else {
      rows.push({
        op: 'push',
        label: 'Push',
        detail: `${String(upstream.ahead)} ${upstream.ahead === 1 ? 'commit' : 'commits'} to ${ref ?? 'upstream'}`,
        enabled: true,
      });
    }
  }
  return rows;
}

export function syncRequestFor(op: SessionGitJobOp): SessionGitSyncRequest {
  if (op === 'fetch') return { op };
  if (op === 'pull') return { op, mode: 'ff_only' };
  return { op, force: false };
}

export function jobTitle(op: SessionGitJobOp): string {
  if (op === 'fetch') return 'Fetching…';
  if (op === 'pull') return 'Pulling…';
  return 'Pushing…';
}

export function jobDoneTitle(op: SessionGitJobOp, ok: boolean): string {
  if (op === 'fetch') return ok ? 'Fetched' : 'Fetch failed';
  if (op === 'pull') return ok ? 'Pulled' : 'Pull failed';
  return ok ? 'Pushed' : 'Push failed';
}

/**
 * One line per job `error_code`, identical on the three clients. `message` is
 * git's own explanation and is rendered beside it in monospace, so only an
 * unknown code falls back to it here.
 */
export function jobErrorCopy(code: string | null | undefined, message: string | null | undefined): string {
  switch (code ?? '') {
    case GIT_JOB_CODE_CANCELLED:
      return 'Cancelled.';
    case GIT_CODE_TIMEOUT:
      return 'git took too long to answer. Try again.';
    case GIT_JOB_CODE_AUTH_REQUIRED:
      return 'No credentials for this remote. Configure a credential helper or SSH agent on the agent host.';
    case GIT_JOB_CODE_NETWORK:
      return "Couldn't reach the remote.";
    case GIT_JOB_CODE_REMOTE_NOT_FOUND:
      return "The remote doesn't exist.";
    case GIT_JOB_CODE_REJECTED:
      return 'The remote rejected the push.';
    case GIT_JOB_CODE_NOT_FAST_FORWARD:
      return 'Your branch is behind the remote.';
    case GIT_JOB_CODE_CONFLICTS:
      return 'Stopped on conflicts — resolve them in the terminal.';
    case GIT_JOB_CODE_DIRTY_WORKTREE:
      return 'Commit or stash your changes first.';
    case GIT_CODE_UNMERGED:
      return 'Resolve the conflicts first.';
    case GIT_JOB_CODE_OPERATION_IN_PROGRESS:
      return 'A rebase or merge is already in progress.';
    case GIT_JOB_CODE_NO_UPSTREAM:
      return 'This branch has no upstream.';
    case GIT_JOB_CODE_NO_REMOTE:
      return 'This repository has no remote.';
    case GIT_JOB_CODE_DETACHED_HEAD:
      return 'Detached HEAD — check out a branch first.';
    case GIT_CODE_NO_HEAD:
      return 'No commits yet.';
    case GIT_CODE_HOOK_FAILED:
      return 'A pre-push hook rejected the push.';
    case GIT_CODE_INDEX_LOCKED:
      return 'Another git process is running — try again.';
    default:
      return message === null || message === undefined || message === '' ? 'git failed.' : message;
  }
}

/** The banner's one line: title plus git's summary, or title plus the failure copy. */
export function jobNotice(job: SessionGitJob): string {
  if (job.status !== 'done') return jobTitle(job.op);
  const ok = job.ok === true;
  const title = jobDoneTitle(job.op, ok);
  if (ok) {
    return job.message === null || job.message === undefined || job.message === ''
      ? title
      : `${title} · ${job.message}`;
  }
  return `${title}: ${jobErrorCopy(job.error_code, job.message)}`;
}

/** Whether the failure's `message` adds anything the copy did not already say. */
export function showsJobOutput(job: SessionGitJob): boolean {
  if (job.ok !== false) return false;
  const message = job.message ?? '';
  return message !== '' && jobErrorCopy(job.error_code, job.message) !== message;
}

export type SyncFollowUp = 'pull_rebase' | 'pull_merge' | 'force_push';

/**
 * What a failed job can be retried as: a pull that could not fast-forward as
 * a rebase or a merge, a rejected push as a push with lease. Each rewrites
 * history somewhere, so every one is confirmed first. A refused publish
 * installs no upstream and a pruned one cannot be pulled until the next
 * fetch, so — like the Pull row — the pulls are dropped when the status says
 * so, never when it is unavailable.
 */
export function jobFollowUps(
  job: SessionGitJob,
  repo?: SessionGitRepoInfo | null,
): SyncFollowUp[] {
  if (job.status !== 'done' || job.ok !== false) return [];
  const pulls: SyncFollowUp[] =
    repo !== null && repo !== undefined && (repo.upstream?.behind ?? null) === null
      ? []
      : ['pull_rebase', 'pull_merge'];
  // Behind the remote: integrate first, whichever way the user prefers; a
  // push may also mean a deliberate rewrite, so the lease push stays offered.
  if (job.error_code === GIT_JOB_CODE_NOT_FAST_FORWARD) {
    return job.op === 'push' ? [...pulls, 'force_push'] : pulls;
  }
  if (job.op === 'push' && job.error_code === GIT_JOB_CODE_REJECTED) return ['force_push'];
  return [];
}

export function followUpLabel(followUp: SyncFollowUp): string {
  if (followUp === 'pull_rebase') return 'Pull with rebase…';
  if (followUp === 'pull_merge') return 'Pull with merge…';
  return 'Force push (with lease)…';
}

export function followUpRequest(followUp: SyncFollowUp): SessionGitSyncRequest {
  if (followUp === 'pull_rebase') return { op: 'pull', mode: 'rebase' };
  if (followUp === 'pull_merge') return { op: 'pull', mode: 'merge' };
  return { op: 'push', force: true };
}

export function followUpPrompt(
  followUp: SyncFollowUp,
  repo: SessionGitRepoInfo | null | undefined,
): { title: string; body: string; confirm: string } {
  const ref = repo?.upstream?.ref ?? 'the upstream';
  const branch = repo?.head.branch ?? 'your branch';
  if (followUp === 'pull_rebase') {
    return {
      title: 'Pull with rebase?',
      body: `Replays your commits on top of ${ref}.`,
      confirm: 'Rebase',
    };
  }
  if (followUp === 'pull_merge') {
    return {
      title: 'Pull with merge?',
      body: `Merges ${ref} into ${branch}.`,
      confirm: 'Merge',
    };
  }
  return {
    title: 'Force push?',
    body: `Overwrites ${ref} with your branch. Proceeds only if the remote hasn't moved since your last fetch.`,
    confirm: 'Force push',
  };
}

export interface RebaseTarget {
  value: string;
  label: string;
}

/**
 * The rebase sheet's picker: local branches, then remote-tracking ones, each
 * A→Z, from the project Git screen's rows. The session's own branch is left out by name
 * — the rows' `is_current` is the project root's checkout, which for a session
 * in a linked worktree is usually the very branch it wants to rebase onto.
 */
export function rebaseTargets(
  items: readonly GitItem[],
  currentBranch: string | null | undefined,
): RebaseTarget[] {
  const usable = (item: GitItem): item is GitItem & { branch: string } =>
    item.branch !== null && item.branch !== undefined && item.branch !== currentBranch;
  const byName = (a: { branch: string }, b: { branch: string }) =>
    compareRefNames(a.branch, b.branch);
  const local = items
    .filter((item) => item.kind === KIND_BRANCH_ONLY || item.kind === KIND_BRANCH_WITH_WORKTREE)
    .filter(usable)
    .sort(byName)
    .map((item) => ({ value: `refs/heads/${item.branch}`, label: item.branch }));
  const remote = items
    .filter((item) => item.kind === KIND_REMOTE_BRANCH)
    .filter(usable)
    .sort(byName)
    .map((item) => ({ value: `refs/remotes/${item.branch}`, label: `${item.branch} (remote)` }));
  return [...local, ...remote];
}

/**
 * A picked target's name for copy: the picker sends fully qualified refs so a
 * tag of the same name cannot shadow the branch, but a notice reads the short one.
 */
export function shortRefName(ref: string): string {
  if (ref.startsWith('refs/heads/')) return ref.slice('refs/heads/'.length);
  if (ref.startsWith('refs/remotes/')) return ref.slice('refs/remotes/'.length);
  return ref;
}

/**
 * Why a rebase cannot start from this status, or null. Untracked files do not
 * block: git refuses only the ones a pick would overwrite.
 */
export function rebaseBlocker(status: SessionGitStatus | undefined): string | null {
  const repo = status?.repo;
  if (repo === undefined || repo === null) return 'Repository unavailable.';
  if (repo.state !== 'clean') return 'A rebase or merge is already in progress.';
  if (repo.head.initial) return 'This branch has no commits yet.';
  const counts = status?.counts;
  if (counts !== undefined && counts !== null && counts.conflicts + counts.staged + counts.unstaged > 0) {
    return 'Commit or discard your changes first.';
  }
  return null;
}

/** The branch a rebase acts on, for copy: the one being rebased, else HEAD's. */
function rebasedBranch(repo: SessionGitRepoInfo | null | undefined): string {
  return repo?.rebase?.branch ?? repo?.head.branch ?? 'the branch';
}

export interface RebaseBanner {
  title: string;
  /** Where it stopped and how far along, when the agent could read it. */
  progress: string | null;
  hint: string;
  /**
   * Whether Continue / Skip / Abort can act on it: `repo.rebase` is null for a
   * `git am` session (which reports `rebasing` too, and refuses all three) and
   * from an agent that predates the rebase routes.
   */
  actionable: boolean;
}

/** The Changes screen's banner while the repository is rebasing. */
export function rebaseBanner(
  repo: SessionGitRepoInfo | null | undefined,
  conflicts: number,
): RebaseBanner | null {
  if (repo === null || repo === undefined || repo.state !== 'rebasing') return null;
  const info = repo.rebase ?? null;
  const branch = info?.branch ?? null;
  const onto = info?.onto_ref ?? info?.onto_short_oid ?? null;
  const title =
    branch !== null && onto !== null
      ? `Rebasing ${branch} onto ${onto}`
      : branch !== null
        ? `Rebasing ${branch}`
        : onto !== null
          ? `Rebasing onto ${onto}`
          : 'Rebase in progress';
  const parts: string[] = [];
  const stopped = info?.stopped_on ?? null;
  if (stopped !== null) parts.push(`Stopped at ${stopped.short_oid} ${stopped.subject}`);
  const step = info?.step ?? null;
  const total = info?.total ?? null;
  if (step !== null && total !== null) parts.push(`${String(step)}/${String(total)}`);
  const actionable = info !== null;
  const hint = !actionable
    ? 'Continue, skip or abort it in the terminal.'
    : conflicts > 0
      ? `${String(conflicts)} ${conflicts === 1 ? 'conflict' : 'conflicts'} — fix the files, then Mark resolved.`
      : 'No conflicts left — Continue when ready.';
  return { title, progress: parts.length === 0 ? null : parts.join(' · '), hint, actionable };
}

export type RebasePromptStep = 'skip' | 'abort';

export function rebaseStepPrompt(
  step: RebasePromptStep,
  repo: SessionGitRepoInfo | null | undefined,
): { title: string; body: string; confirm: string } {
  const branch = rebasedBranch(repo);
  if (step === 'skip') {
    const stopped = repo?.rebase?.stopped_on ?? null;
    const commit = stopped === null ? 'The commit the rebase stopped at' : `${stopped.short_oid} “${stopped.subject}”`;
    return {
      title: 'Skip this commit?',
      body: `${commit} will be left out of ${branch}.`,
      confirm: 'Skip commit',
    };
  }
  return {
    title: 'Abort the rebase?',
    body: `${branch} goes back to where it was before the rebase. Conflict resolutions made so far are discarded.`,
    confirm: 'Abort rebase',
  };
}

/**
 * The notice a finished rebase call leaves. `onto` is what the user picked,
 * known only for a start; Continue and Skip finish without it.
 */
export function rebaseOutcomeNotice(
  result: SessionGitRebaseResult,
  branch: string,
  onto: string | null,
): { tone: 'ok' | 'danger'; text: string } {
  const ontoName = onto === null ? null : shortRefName(onto);
  switch (result.outcome) {
    case GIT_REBASE_REBASED:
      return {
        tone: 'ok',
        text: ontoName === null ? 'Rebase finished.' : `Rebased ${branch} onto ${ontoName}.`,
      };
    case GIT_REBASE_UP_TO_DATE:
      return { tone: 'ok', text: `${branch} is already up to date with ${ontoName ?? 'that branch'}.` };
    case GIT_REBASE_ABORTED:
      return { tone: 'ok', text: `Rebase aborted — ${branch} is back where it started.` };
    case GIT_REBASE_STOPPED: {
      const message = result.message ?? null;
      if (message !== null && message !== '') return { tone: 'danger', text: `Rebase stopped: ${message}` };
      const subject = result.rebase?.stopped_on?.subject ?? null;
      return {
        tone: 'danger',
        text:
          subject === null
            ? 'Rebase stopped — resolve the conflicts, then Continue.'
            : `Rebase stopped at “${subject}” — resolve the conflicts, then Continue.`,
      };
    }
    default:
      return { tone: 'ok', text: `Rebase: ${result.outcome}` };
  }
}

/**
 * Copy for a failed rebase call: the rebase-specific readings of the shared
 * refusal codes, then the mutations' copy.
 */
export function rebaseErrorCopy(error: unknown): string {
  if (isSessionGitError(error) && error.kind === 'git') {
    switch (error.code) {
      case GIT_CODE_UNKNOWN_REF:
        return error.detailMessage === null || error.detailMessage === ''
          ? "That isn't a branch, tag or commit here."
          : `“${error.detailMessage}” isn't a branch, tag or commit here.`;
      case GIT_CODE_NO_REBASE:
        return 'No rebase is in progress anymore.';
      case GIT_JOB_CODE_DIRTY_WORKTREE:
        return 'Local changes are in the way — stage, commit or discard them first.';
      case GIT_JOB_CODE_OPERATION_IN_PROGRESS:
        return 'A rebase or merge is already in progress.';
      case GIT_CODE_NO_HEAD:
        return 'This branch has no commits yet.';
      case GIT_CODE_HOOK_FAILED:
        return 'The pre-rebase hook refused the rebase.';
      case GIT_CODE_TOO_OLD:
        return 'Rebasing needs git 2.26 or newer on the agent machine.';
      case GIT_CODE_TIMEOUT:
        return 'The rebase took too long and was cut off — continue or abort it.';
      default:
        break;
    }
  }
  return mutationErrorCopy(error);
}

/** git's own output worth showing under the copy: which files, or what the hook said. */
export function rebaseErrorOutput(error: unknown): string | null {
  if (!isSessionGitError(error) || error.kind !== 'git') return null;
  if (error.code !== GIT_JOB_CODE_DIRTY_WORKTREE && error.code !== GIT_CODE_HOOK_FAILED) return null;
  return error.detailMessage === null || error.detailMessage === '' ? null : error.detailMessage;
}

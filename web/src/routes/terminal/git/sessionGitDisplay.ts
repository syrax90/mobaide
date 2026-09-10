/**
 * Row and copy derivations for the in-session Changes viewer — the port of
 * Android `GitChangesUiState.kt` / `GitDiffUiState.kt` and iOS
 * `GitChangesModel` / `GitDiffModel` / `SessionGitCopy`, which agree line for
 * line. Pure, so the three clients' strings can be diffed against each other.
 */
import { isApiError } from '../../../api/errors';
import {
  GIT_CODE_ERROR,
  GIT_CODE_TIMEOUT,
  GIT_CODE_UNAVAILABLE,
  isSessionGitError,
  type SessionGitDiff,
  type SessionGitDiffScope,
  type SessionGitEntry,
  type SessionGitHunk,
  type SessionGitRepoInfo,
  type SessionGitStatus,
  type SessionGitUpstream,
} from '../../../api/sessionGitRepository';

export const DIFF_CONTEXT_DEFAULT = 3;
export const DIFF_CONTEXT_CHOICES: readonly number[] = [3, 10, 25];

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

export function fileName(path: string): string {
  return path.slice(path.lastIndexOf('/') + 1);
}

export function directory(path: string): string | null {
  const cut = path.lastIndexOf('/');
  return cut <= 0 ? null : path.slice(0, cut);
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
        return `git refused the request (${code}).`;
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

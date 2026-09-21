/**
 * Derivations and copy for the conflict resolver — the port of Android
 * `GitConflictUiState.kt` and iOS `GitConflictDisplay`, which agree line for
 * line. Pure, so the three clients' strings can be diffed against each other.
 *
 * The wire says `ours` / `theirs` (index stages 2 and 3). The UI never does:
 * during a rebase "ours" is the branch being rebased *onto*, the opposite of
 * what the word suggests, so every string names a side by its label and role.
 */
import {
  GIT_CONFLICT_KIND_TEXT,
  type SessionGitConflict,
  type SessionGitConflictSide,
} from '../../../api/sessionGitRepository';
import { fileName } from './sessionGitDisplay';

/**
 * The agent's marker test, `^(<{7,}|>{7,})(\s|$)` over bytes: Python's
 * bytes-mode `\s` is exactly these six characters, so the class is spelled out
 * rather than left to the wider Unicode `\s` of JavaScript.
 */
const MARKER_LINE = /^(?:<{7,}|>{7,})(?:[ \t\n\r\f\v]|$)/;
const START_LINE = /^<{7,}(?:[ \t\n\r\f\v]|$)/;
const BASE_LINE = /^\|{7,}(?:[ \t\n\r\f\v]|$)/;

export interface ConflictScan {
  /** Conflict blocks still open: lines starting a `<<<<<<<` marker. */
  conflicts: number;
  /** Any `<<<<<<<` or `>>>>>>>` line — what the agent refuses to mark resolved. */
  hasMarkers: boolean;
  /** `|||||||` sections: the original lines are in the text. */
  hasBase: boolean;
}

export function scanConflictText(text: string): ConflictScan {
  let conflicts = 0;
  let hasMarkers = false;
  let hasBase = false;
  for (const line of text.split('\n')) {
    if (START_LINE.test(line)) conflicts += 1;
    if (!hasMarkers && MARKER_LINE.test(line)) hasMarkers = true;
    if (!hasBase && BASE_LINE.test(line)) hasBase = true;
  }
  return { conflicts, hasMarkers, hasBase };
}

/**
 * The offset of the next `<<<<<<<` line after `from`, wrapping to the first;
 * -1 when none is left. A caret parked on a marker line moves on to the next.
 */
export function nextConflictOffset(text: string, from: number): number {
  const starts: number[] = [];
  let offset = 0;
  for (const line of text.split('\n')) {
    if (START_LINE.test(line)) starts.push(offset);
    offset += line.length + 1;
  }
  if (starts.length === 0) return -1;
  return starts.find((start) => start > from) ?? starts[0] ?? -1;
}

/**
 * Whether `text` is more than `byteLimit` UTF-8 bytes — what the agent would
 * refuse, and past a few MiB what would not even fit the relay's tunnel frame.
 * UTF-8 is never shorter than UTF-16, so most texts are settled by `length`.
 */
export function exceedsByteLimit(text: string, byteLimit: number): boolean {
  if (text.length > byteLimit) return true;
  if (text.length * 3 <= byteLimit) return false;
  return new TextEncoder().encode(text).length > byteLimit;
}

export function tooLargeToSaveNotice(byteLimit: number): string {
  return `Too large to save here (over ${String(Math.round(byteLimit / 1024))} KB).`;
}

export function conflictsLeftLabel(conflicts: number, hasMarkers: boolean): string {
  if (conflicts === 0) return hasMarkers ? 'A conflict marker is left' : 'No conflict markers left';
  return conflicts === 1 ? '1 conflict left' : `${String(conflicts)} conflicts left`;
}

export function sideName(conflict: SessionGitConflict, side: SessionGitConflictSide): string {
  const label = (side === 'ours' ? conflict.ours.label : conflict.theirs.label) ?? null;
  if (label !== null && label !== '') return label;
  return side === 'ours' ? 'Current branch' : 'Incoming change';
}

export function sideRole(state: string, side: SessionGitConflictSide): string {
  if (state === 'rebasing') {
    return side === 'ours'
      ? 'the branch you are rebasing onto, plus your commits already replayed'
      : 'your commit being replayed';
  }
  if (state === 'merging') {
    return side === 'ours' ? 'the branch you are on' : 'the branch being merged in';
  }
  return side === 'ours' ? 'what you have checked out' : 'the change being applied';
}

/** What happened to the file, in the sides' own names. */
export function conflictCaption(conflict: SessionGitConflict): string {
  const ours = sideName(conflict, 'ours');
  const theirs = sideName(conflict, 'theirs');
  switch (conflict.code) {
    case 'UU':
      return 'Both sides changed this file.';
    case 'AA':
      return 'Both sides added this file.';
    case 'DU':
      return `${ours} deleted this file; ${theirs} changed it.`;
    case 'UD':
      return `${ours} changed this file; ${theirs} deleted it.`;
    case 'DD':
      return 'Both sides deleted this file.';
    case 'AU':
      return `Only ${ours} has this file.`;
    case 'UA':
      return `Only ${theirs} has this file.`;
    default:
      return 'The two sides disagree about this file.';
  }
}

/** How to read the markers: which region of a conflict block is which side. */
export function conflictLegend(conflict: SessionGitConflict, hasBase: boolean): string[] {
  const ours = `${sideName(conflict, 'ours')} — ${sideRole(conflict.state ?? 'clean', 'ours')}.`;
  const theirs = `${sideName(conflict, 'theirs')} — ${sideRole(conflict.state ?? 'clean', 'theirs')}.`;
  if (!hasBase) {
    return [`Between <<<<<<< and =======: ${ours}`, `Between ======= and >>>>>>>: ${theirs}`];
  }
  return [
    `Between <<<<<<< and |||||||: ${ours}`,
    'Between ||||||| and =======: the original lines, before either change.',
    `Between ======= and >>>>>>>: ${theirs}`,
  ];
}

export function isEditableConflict(conflict: SessionGitConflict): boolean {
  return conflict.kind === GIT_CONFLICT_KIND_TEXT && typeof conflict.content === 'string';
}

/** Why a conflict cannot be edited here; null for an editable one. */
export function conflictKindNotice(conflict: SessionGitConflict): string | null {
  switch (conflict.kind) {
    case GIT_CONFLICT_KIND_TEXT:
      return null;
    case 'too_large':
      return `This file is too large to edit here (over ${String(Math.round((conflict.byte_limit ?? 0) / 1024))} KB). Pick a side, or resolve it in the terminal.`;
    case 'binary':
      return 'This is a binary file — pick a side.';
    case 'encoding':
      return 'This file is not UTF-8 text — pick a side, or resolve it in the terminal.';
    case 'missing':
      return 'The file is not in the working tree — pick a side.';
    case 'symlink':
      return 'This is a symbolic link — pick a side.';
    case 'submodule':
      return 'Submodule conflicts are resolved in the terminal.';
    default:
      return "This conflict can't be resolved here — use the terminal.";
  }
}

const TAKEABLE_KINDS: ReadonlySet<string> = new Set([
  GIT_CONFLICT_KIND_TEXT,
  'too_large',
  'binary',
  'encoding',
  'missing',
  'symlink',
]);

/** Whole-file picks exist for every known shape but a submodule or a directory in the way. */
export function canTakeSide(conflict: SessionGitConflict): boolean {
  return TAKEABLE_KINDS.has(conflict.kind);
}

/** `git checkout --merge` needs both sides' versions to rebuild the markers. */
export function canStartOver(conflict: SessionGitConflict): boolean {
  return (
    isEditableConflict(conflict) &&
    conflict.ours.present === true &&
    conflict.theirs.present === true
  );
}

export interface TakeChoice {
  side: SessionGitConflictSide;
  label: string;
  title: string;
  body: string;
  confirm: string;
  /** Removing the file is the destructive pick. */
  danger: boolean;
}

/**
 * The whole-file picks. Both sides present: use one or the other. Otherwise a
 * side that lacks the file means "delete" and the one that has it "keep";
 * when neither has it there is a single way out.
 */
export function takeChoices(conflict: SessionGitConflict): TakeChoice[] {
  if (!canTakeSide(conflict)) return [];
  const file = fileName(conflict.path);
  const oursPresent = conflict.ours.present === true;
  const theirsPresent = conflict.theirs.present === true;
  const choice = (side: SessionGitConflictSide): TakeChoice => {
    const name = sideName(conflict, side);
    const present = side === 'ours' ? oursPresent : theirsPresent;
    if (oursPresent && theirsPresent) {
      return {
        side,
        label: `Use ${name} for the whole file`,
        title: `Use ${name} for the whole file?`,
        body: `Every conflict in ${file} is settled with this side and the file is marked resolved. Edits made to it are discarded.`,
        confirm: 'Use this side',
        danger: false,
      };
    }
    if (present) {
      return {
        side,
        label: `Keep the file (${name})`,
        title: `Keep ${file}?`,
        body: `The file stays as ${name} has it and is marked resolved.`,
        confirm: 'Keep file',
        danger: false,
      };
    }
    return {
      side,
      label: oursPresent || theirsPresent ? `Delete the file (${name})` : 'Delete the file',
      title: `Delete ${file}?`,
      body: `The file is removed, as on ${name}, and the conflict is marked resolved.`,
      confirm: 'Delete file',
      danger: true,
    };
  };
  if (!oursPresent && !theirsPresent) return [choice('ours')];
  return [choice('ours'), choice('theirs')];
}

export function startOverPrompt(path: string): { title: string; body: string } {
  return {
    title: 'Start over?',
    body: `${fileName(path)} is rebuilt from both sides. Every edit made to it since the conflict — here or in the terminal — is discarded.`,
  };
}

export const START_OVER_LABEL = 'Start over';
export const START_OVER_WITH_BASE_LABEL = 'Start over with original lines';

export function restorePrompt(path: string): {
  title: string;
  body: string;
  confirm: string;
} {
  return {
    title: 'Restore the conflict?',
    body: `${fileName(path)} goes back to its conflicted state. The resolution you applied is discarded.`,
    confirm: 'Restore conflict',
  };
}

export function discardEditsPrompt(path: string): {
  title: string;
  body: string;
} {
  return {
    title: 'Discard your edits?',
    body: `Your changes to ${fileName(path)} have not been saved.`,
  };
}

export function resolvedNotice(path: string): string {
  return `Resolved ${path}.`;
}

export function draftSavedNotice(path: string): string {
  return `Saved ${path} — the conflict is still open.`;
}

export function restoredNotice(path: string): string {
  return `Restored the conflict in ${path}.`;
}

export function fileChangedNotice(path: string): string {
  return `${fileName(path)} changed on the agent machine since you opened it.`;
}

export const KEEP_EDITS_WARNING = 'Applying will overwrite the other change.';

export function resolvedElsewhereNotice(path: string): string {
  return `${fileName(path)} is no longer in conflict.`;
}

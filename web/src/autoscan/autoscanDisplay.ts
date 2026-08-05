/**
 * How an Auto Scan renders. Pure, so the parity rules are testable without a DOM
 * — the same split `routes/sessions/sessionDisplay.ts` uses.
 *
 * Tones reuse the session vocabulary rather than inventing a second one: a
 * scanning project and a running session mean the same thing to the reader, and
 * the CSS tokens already exist.
 */
import type { AutoScanInstance } from '../protocol/dto/autoscan';
import type { PhaseStatus } from './autoscanModel';

export type AutoScanTone = 'running' | 'pending' | 'stopped' | 'crashed';

/**
 * Status → tone. `status` is an open wire string, so the default matters: an
 * unrecognised value renders as inert rather than as a false "running".
 */
export function autoScanTone(status: string): AutoScanTone {
  switch (status) {
    case 'scanning':
    case 'running':
    case 'connected':
      return 'running';
    case 'idle':
    case 'starting':
    case 'connecting':
    case 'replaying':
      return 'pending';
    case 'error':
    case 'failed':
    case 'disconnected':
      return 'crashed';
    default:
      return 'stopped';
  }
}

/** Status → chip label, capitalising anything the clients do not name. */
export function autoScanStatusLabel(status: string): string {
  switch (status) {
    case 'scanning':
      return 'Scanning';
    case 'idle':
      return 'Idle';
    case 'stopped':
      return 'Stopped';
    case 'error':
      return 'Error';
    case 'starting':
      return 'Starting';
    default:
      return status === '' ? '' : status.charAt(0).toUpperCase() + status.slice(1);
  }
}

export function phaseTone(status: PhaseStatus): AutoScanTone {
  switch (status) {
    case 'active':
      return 'pending';
    case 'ok':
      return 'running';
    case 'failed':
      return 'crashed';
    default:
      return 'stopped';
  }
}

export function issueTone(status: string): AutoScanTone {
  switch (status) {
    case 'started':
      return 'pending';
    case 'ok':
      return 'running';
    case 'failed':
      return 'crashed';
    default:
      return 'stopped';
  }
}

const PHASE_LABELS: Readonly<Record<string, string>> = {
  scan: 'Scan',
  state: 'State',
  label: 'Label',
  worktree: 'Worktree',
  spawn: 'Spawn',
};

export function phaseLabel(key: string): string {
  return Object.hasOwn(PHASE_LABELS, key) ? (PHASE_LABELS[key] ?? key) : key;
}

/**
 * "3m ago" for an epoch-seconds timestamp.
 *
 * `nowMs` is injected rather than read from the clock so the rendering is
 * testable and so a list re-render cannot show two rows computed against
 * different instants.
 */
export function relativeTime(epochSeconds: number, nowMs: number): string {
  if (epochSeconds <= 0) return '';
  const elapsed = Math.floor(nowMs / 1000 - epochSeconds);
  if (elapsed < 5) return 'just now';
  if (elapsed < 60) return `${String(elapsed)}s ago`;
  if (elapsed < 3600) return `${String(Math.floor(elapsed / 60))}m ago`;
  if (elapsed < 86400) return `${String(Math.floor(elapsed / 3600))}h ago`;
  return `${String(Math.floor(elapsed / 86400))}d ago`;
}

/**
 * Parse the agent's `next_scan_at`.
 *
 * The agent emits `datetime.isoformat()`, so `+00:00` rather than `Z`, and with
 * fractional seconds — the shared fixtures use the `Z` spelling instead. `Date`
 * accepts all three, but a naive string with no offset would be read as local
 * time, so those are rejected: a wrong countdown is worse than none.
 */
export function parseNextScanAt(value: string | undefined): number | null {
  if (value === undefined || value === '') return null;
  if (!/(?:Z|[+-]\d{2}:?\d{2})$/.test(value)) return null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

/**
 * "Next scan in 2m 5s", or `null` when there is no scheduled scan.
 *
 * Unpadded, matching iOS `CountdownView` verbatim; the width jitter that would
 * otherwise cause is handled the same way it is there, with tabular figures in
 * the stylesheet rather than with a different string.
 */
export function countdownLabel(nextScanAt: string | undefined, nowMs: number): string | null {
  const target = parseNextScanAt(nextScanAt);
  if (target === null) return null;
  const secondsLeft = Math.round((target - nowMs) / 1000);
  if (secondsLeft <= 0) return 'Next scan in now';
  const minutes = Math.floor(secondsLeft / 60);
  const seconds = secondsLeft % 60;
  return minutes > 0
    ? `Next scan in ${String(minutes)}m ${String(seconds)}s`
    : `Next scan in ${String(seconds)}s`;
}

/** Slots left before `POST /api/autoscan/start` answers 429. */
export function remainingScanSlots(list: {
  capacity: number;
  max_scans: number;
}): number {
  // `capacity` is how many scans are *running* — the agent's naming, not the
  // headroom it sounds like.
  return Math.max(0, list.max_scans - list.capacity);
}

/** Newest activity first, then by name so the order is stable while idle. */
export function sortScans(scans: readonly AutoScanInstance[]): AutoScanInstance[] {
  return [...scans].sort(
    (left, right) =>
      right.last_activity_at - left.last_activity_at ||
      left.project_name.localeCompare(right.project_name),
  );
}

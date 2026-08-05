/**
 * The two 409 bodies the agent produces, in one place.
 *
 * Mirrors iOS `Data/Repository/ConflictParsing.swift`, which Android splits
 * across `ProjectsRepository.parseDetail` and `GitRepository.parseBusyBy`. The
 * shapes are genuinely different and neither is in an OpenAPI schema:
 *
 * * projects — `{"detail": "Cannot delete project 'x': auto-scan is active…"}`,
 *   a human string, raised for a duplicate name, a rename collision, or an
 *   active auto-scan.
 * * git delete — `{"detail": {"busy_by": [BusyHolder, …]}}`, structured, raised
 *   before anything is deleted when a worktree in the batch is held.
 *
 * A future 409 producer reuses this module rather than growing a third parser.
 */
import { busyByResponseSchema, type BusyHolder } from '../protocol/dto/gitConflict';
import { isApiError } from './errors';

/** The generic label mobile falls back to when the body carries nothing usable. */
const FALLBACK_CONFLICT = 'Conflict';

export function isConflict(error: unknown): boolean {
  return isApiError(error) && error.status === 409;
}

/**
 * The human `detail` of a string-shaped conflict.
 *
 * `toApiError` has already lifted a string `detail` into `message`, so the
 * common path is one property read; the fallback keeps Android's ladder
 * (detail → raw message → "Conflict") for a body that is shaped differently.
 */
export function conflictDetailString(error: unknown): string {
  if (!isApiError(error)) return FALLBACK_CONFLICT;
  if (typeof error.detail === 'string' && error.detail !== '') return error.detail;
  return error.message === '' ? FALLBACK_CONFLICT : error.message;
}

/**
 * The holders of a git busy-conflict, or `null` when the body is not that shape.
 *
 * `null` is meaningfully different from `[]`: it means "this 409 was not a busy
 * conflict", which the caller reports as a plain message instead of opening a
 * dialog listing nobody. Android returns an empty list here and ends up showing
 * an empty dialog; iOS distinguishes the two, and this follows iOS.
 */
export function busyHolders(error: unknown): BusyHolder[] | null {
  if (!isApiError(error)) return null;
  const decoded = busyByResponseSchema.safeParse(error.detail);
  return decoded.success ? decoded.data.busy_by : null;
}

/** One holder line, verbatim from `GitScreen.busyLine` / `BusyConflictDialog`. */
export function busyHolderLine(holder: BusyHolder): string {
  switch (holder.kind) {
    case 'session':
      return `Session ${holder.session_id ?? '?'}: ${holder.cwd ?? '?'}`;
    case 'auto_scan':
      return `Auto-scan ${holder.project_name ?? '?'}: ${holder.worktree_path ?? '?'}`;
    default:
      return `${holder.kind}: ${holder.cwd ?? holder.worktree_path ?? '?'}`;
  }
}

/**
 * The base-branch picker's pure logic: which ref is preselected, which one a
 * launch sends, and how the list filters. Shared rules with Android and iOS
 * (PROTOCOL.md, "Delta 2026-11-09: worktree base branch").
 */
import type { WorktreeBaseRef, WorktreeBases } from '../../../api/gitRepository';
import { parseNextScanAt, relativeTime } from '../../../autoscan/autoscanDisplay';

/** A manual pick, tagged with the project it was made for so a project change drops it. */
export interface WorktreeBasePick {
  projectName: string;
  /** `null` is "Default branch": the agent's own start point. */
  ref: string | null;
}

/** `last_used`, then `default`, then nothing (the agent's own default). */
export function preselectedWorktreeBase(bases: WorktreeBases | undefined): string | null {
  if (bases === undefined) return null;
  return bases.last_used ?? bases.default ?? null;
}

/**
 * The base a launch sends for `projectName`. The user's pick wins while it
 * belongs to this project and still names a listed ref; otherwise the
 * preselection. Without a loaded list (loading, error) the agent default.
 */
export function effectiveWorktreeBase(
  pick: WorktreeBasePick | null,
  projectName: string | null,
  bases: WorktreeBases | undefined,
): string | null {
  if (projectName === null) return null;
  if (pick?.projectName === projectName) {
    if (pick.ref === null) return null;
    if (bases === undefined || bases.refs.some((entry) => entry.ref === pick.ref)) {
      return pick.ref;
    }
  }
  return preselectedWorktreeBase(bases);
}

/** Case-insensitive substring match on the ref name; order is the agent's. */
export function filterWorktreeBases(
  refs: readonly WorktreeBaseRef[],
  query: string,
): WorktreeBaseRef[] {
  const needle = query.trim().toLowerCase();
  if (needle === '') return [...refs];
  return refs.filter((entry) => entry.ref.toLowerCase().includes(needle));
}

/** "3d ago" for a tip commit, or `''` when the agent sent no usable time. */
export function commitAge(committedAt: string | null | undefined, nowMs: number): string {
  const parsed = parseNextScanAt(committedAt ?? undefined);
  if (parsed === null) return '';
  return relativeTime(Math.floor(parsed / 1000), nowMs);
}

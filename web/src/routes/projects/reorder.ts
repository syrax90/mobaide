/**
 * Optimistic reorder, reconciled against the echoed snapshot.
 *
 * `PATCH /api/projects/order` takes the **whole** name list and 400s on any
 * duplicate, missing or unknown name, so a reorder is never a delta. The
 * optimistic order is held as an *override* rather than written over the cache:
 * the cache stays whatever the server last said, and the override decides how it
 * is displayed until the server agrees. That is the Android `combine` / iOS
 * `recompute` algorithm, and it is what keeps a two-client test flicker-free —
 * the echo arrives with the new order, matches, and the override simply drops.
 */
import type { ProjectInfo } from '../../api/projectRepository';

export function moveItem<T>(items: readonly T[], from: number, to: number): T[] {
  const next = [...items];
  if (from < 0 || from >= next.length || to < 0 || to >= next.length || from === to) {
    return next;
  }
  const [moved] = next.splice(from, 1);
  // `noUncheckedIndexedAccess` makes the splice result `T | undefined`, and the
  // bounds check above is not visible to the compiler.
  if (moved === undefined) return next;
  next.splice(to, 0, moved);
  return next;
}

/** Server order — the list as it arrives, already sorted by the repository. */
function serverNames(projects: readonly ProjectInfo[]): string[] {
  return projects.map((project) => project.name);
}

export interface ReconciledOrder {
  projects: ProjectInfo[];
  /** The override to keep holding, or `null` once the server has caught up. */
  pendingOrder: string[] | null;
}

/**
 * Apply a pending order to a fresh snapshot.
 *
 * The override is released only when the snapshot's own order **exactly** equals
 * it. Releasing on anything less — say, on the first snapshot to arrive after
 * the PATCH — would let an unrelated `projects.changed` (someone renaming a
 * project from their phone) drop the override mid-flight and snap the list back.
 *
 * A name the override does not mention sorts last, which is what a project
 * created by another client does while a reorder is in flight.
 */
export function reconcileOrder(
  projects: readonly ProjectInfo[],
  pendingOrder: readonly string[] | null,
): ReconciledOrder {
  if (pendingOrder === null) {
    return { projects: [...projects], pendingOrder: null };
  }
  const names = serverNames(projects);
  if (names.length === pendingOrder.length && names.every((n, i) => n === pendingOrder[i])) {
    return { projects: [...projects], pendingOrder: null };
  }
  const rank = new Map(pendingOrder.map((name, index) => [name, index]));
  const ordered = [...projects].sort(
    (a, b) =>
      (rank.get(a.name) ?? Number.MAX_SAFE_INTEGER) -
      (rank.get(b.name) ?? Number.MAX_SAFE_INTEGER),
  );
  return { projects: ordered, pendingOrder: [...pendingOrder] };
}

/** What the live region says after a keyboard or button move. */
export function moveAnnouncement(name: string, index: number, total: number): string {
  return `${name} moved to position ${String(index + 1)} of ${String(total)}`;
}

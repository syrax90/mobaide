/**
 * Ad-hoc working directories this browser has launched sessions against, per
 * agent.
 *
 * Device-local on all three clients — nothing about a recent crosses the wire,
 * only the `cwd` of the launch that produced it. The key shape is Android's and
 * iOS's verbatim (`recent_custom_paths::<agentKey>`, a JSON array newest-first)
 * rather than this client's `mobaide-*` zustand convention, so the bucket stays
 * recognisable across the three. One consequence is worth having: the agent id
 * is a *storage* key here and never an object key, so none of the `__proto__`
 * guards `lastSessionStore` and `terminalPrefsStore` need apply.
 *
 * An empty `agentId` is mobile's null `agentScopeKey`: reads and writes are
 * both skipped rather than sharing one unscoped bucket.
 */

/** Android `PreferencesStore.MAX_RECENT_CUSTOM_PATHS`, iOS `maxRecentCustomPaths`. */
export const MAX_RECENT_CUSTOM_PATHS = 5;

function storageKey(agentId: string): string {
  return `recent_custom_paths::${agentId}`;
}

function writePaths(agentId: string, paths: readonly string[]): void {
  const key = storageKey(agentId);
  try {
    if (paths.length === 0) {
      // Drop the key outright rather than parking an "[]" blob, matching iOS's
      // `removeRecentCustomPath`: the key embeds an agent id that may never
      // come back.
      localStorage.removeItem(key);
    } else {
      localStorage.setItem(key, JSON.stringify(paths));
    }
  } catch {
    // Storage refused — a partitioned context, a full quota, Safari private
    // mode. Deliberately no in-memory mirror like `agentTokens` keeps: a
    // suggestion that reappears after a reload is a cosmetic loss, not a dead
    // auth axis, and mirroring would resurrect entries another tab forgot.
  }
}

export function readRecentPaths(agentId: string): string[] {
  if (agentId === '') return [];
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(storageKey(agentId));
  } catch {
    return [];
  }
  if (raw === null) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const entries: unknown[] = parsed;
    // One bad member voids the whole blob rather than being filtered out, which
    // is what both mobile clients do: their decoders are typed (`List<String>`,
    // `[String]`), so a mixed array fails outright instead of salvaging the
    // readable entries. Only a hand-edited value can reach this — writes never
    // produce a blank or a non-string.
    const paths = entries.filter(
      (entry): entry is string => typeof entry === 'string' && entry.trim() !== '',
    );
    if (paths.length !== entries.length) return [];
    return paths.slice(0, MAX_RECENT_CUSTOM_PATHS);
  } catch {
    return [];
  }
}

/**
 * Move `path` to the front, capped and deduped by exact string equality. Only
 * surrounding whitespace is stripped and nothing else is normalised, so
 * `/srv/app` and `/srv/app/` stay distinct exactly as they do on both mobile
 * clients.
 */
export function noteRecentPath(agentId: string, path: string): void {
  if (agentId === '') return;
  const trimmed = path.trim();
  if (trimmed === '') return;
  const existing = readRecentPaths(agentId).filter((entry) => entry !== trimmed);
  writePaths(agentId, [trimmed, ...existing].slice(0, MAX_RECENT_CUSTOM_PATHS));
}

/** Drop one entry, matching on the same trimmed exact string `noteRecentPath` dedupes on. */
export function forgetRecentPath(agentId: string, path: string): void {
  if (agentId === '') return;
  const trimmed = path.trim();
  writePaths(
    agentId,
    readRecentPaths(agentId).filter((entry) => entry !== trimmed),
  );
}

/** Drop the whole bucket. Called when the agent itself is removed. */
export function clearRecentPaths(agentId: string): void {
  if (agentId === '') return;
  writePaths(agentId, []);
}

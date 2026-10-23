/**
 * Folder suggestions for host-path fields, over `GET /api/fs/dirs`.
 *
 * The cost model is the point: a directory is listed once and every further
 * keystroke inside it filters that listing locally, so a request happens only
 * when the user crosses a separator — or when the agent had to truncate a huge
 * directory, in which case the typed fragment goes up as `q`. Android
 * (`PathCompletion.kt`) and iOS (`PathCompletion.swift`) port this module rule
 * for rule.
 */

export const MAX_VISIBLE_SUGGESTIONS = 8;
export const SUGGESTION_DEBOUNCE_MS = 200;
export const LISTING_TTL_MS = 30_000;
const MAX_CACHED_LISTINGS = 32;

const WINDOWS_DRIVE_ROOT = /^[A-Za-z]:[\\/]/;
const WINDOWS_DRIVE_ROOT_ONLY = /^[A-Za-z]:[\\/]$/;

export interface DirListing {
  /** The listed directory after the agent's `~` expansion. */
  path: string;
  dirs: readonly string[];
  truncated: boolean;
}

/** `dir` is what gets listed (kept verbatim, separator included); `fragment` filters it. */
export interface PathQuery {
  dir: string;
  fragment: string;
}

/**
 * Split typed text into the directory to list and the partial name after it.
 * `null` when the text cannot name an absolute directory yet: empty, relative,
 * a bare drive letter, or `~user` without a separator.
 */
export function splitPathQuery(text: string): PathQuery | null {
  if (text === '~') return { dir: '~/', fragment: '' };
  if (!(text.startsWith('/') || text.startsWith('~') || WINDOWS_DRIVE_ROOT.test(text))) {
    return null;
  }
  const lastSep = Math.max(text.lastIndexOf('/'), text.lastIndexOf('\\'));
  if (lastSep < 0) return null;
  return { dir: text.slice(0, lastSep + 1), fragment: text.slice(lastSep + 1) };
}

/**
 * Names matching `fragment`, case-insensitively: prefix matches first, then
 * names that merely contain it, each group in the agent's order. Dot
 * directories stay hidden until the fragment itself starts with a dot.
 */
export function rankDirs(dirs: readonly string[], fragment: string): string[] {
  const needle = fragment.toLowerCase();
  const showHidden = fragment.startsWith('.');
  const prefix: string[] = [];
  const contains: string[] = [];
  for (const name of dirs) {
    if (!showHidden && name.startsWith('.')) continue;
    const lower = name.toLowerCase();
    if (lower.startsWith(needle)) prefix.push(name);
    else if (lower.includes(needle)) contains.push(name);
  }
  return [...prefix, ...contains];
}

/** The separator a listing of `listedPath` is extended with: `\` only for an all-backslash path. */
export function pathSeparator(listedPath: string): string {
  return listedPath.includes('\\') && !listedPath.includes('/') ? '\\' : '/';
}

/** The field's new text after picking `name` from a listing of `listedPath`. */
export function acceptSuggestion(listedPath: string, name: string): string {
  const sep = pathSeparator(listedPath);
  const base = /[\\/]$/.test(listedPath) ? listedPath : listedPath + sep;
  return base + name + sep;
}

/**
 * Drop the one trailing separator a picked suggestion leaves behind, so
 * `/a/b/` and `/a/b` are the same recent path. Roots keep theirs.
 */
export function trimTrailingSeparator(path: string): string {
  if (path.length <= 1 || WINDOWS_DRIVE_ROOT_ONLY.test(path)) return path;
  return /[\\/]$/.test(path) ? path.slice(0, -1) : path;
}

interface CachedListing {
  listing: DirListing;
  query: string | null;
  storedAt: number;
}

export interface CacheHit {
  listing: DirListing;
  /** False for a truncated listing: show it, but still ask the agent with `q`. */
  complete: boolean;
}

/**
 * Per-agent LRU of listings keyed by `(dir, q)`. A listing answers a lookup
 * locally when it is untruncated and either unfiltered or filtered by a `q`
 * the fragment contains — every name containing the fragment then contains
 * `q` too, so nothing the agent would return is missing.
 */
export class DirListingCache {
  private readonly entries = new Map<string, CachedListing>();
  private readonly now: () => number;

  constructor(now: () => number = Date.now) {
    this.now = now;
  }

  lookup(dir: string, fragment: string): CacheHit | undefined {
    const needle = fragment.toLowerCase();
    let partial: DirListing | undefined;
    for (const [key, entry] of this.entries) {
      if (!key.startsWith(`${dir}\u0000`)) continue;
      if (this.now() - entry.storedAt > LISTING_TTL_MS) {
        this.entries.delete(key);
        continue;
      }
      const covers = entry.query === null || needle.includes(entry.query.toLowerCase());
      if (!covers) continue;
      if (!entry.listing.truncated) {
        this.touch(key, entry);
        return { listing: entry.listing, complete: true };
      }
      partial ??= entry.listing;
    }
    return partial === undefined ? undefined : { listing: partial, complete: false };
  }

  /** Whether an unfiltered listing of `dir` is cached (truncated or not). */
  hasUnfiltered(dir: string): boolean {
    const entry = this.entries.get(cacheKey(dir, null));
    return entry !== undefined && this.now() - entry.storedAt <= LISTING_TTL_MS;
  }

  store(dir: string, query: string | null, listing: DirListing): void {
    const key = cacheKey(dir, query);
    this.entries.delete(key);
    this.entries.set(key, { listing, query, storedAt: this.now() });
    while (this.entries.size > MAX_CACHED_LISTINGS) {
      const oldest = this.entries.keys().next().value;
      if (oldest === undefined) break;
      this.entries.delete(oldest);
    }
  }

  private touch(key: string, entry: CachedListing): void {
    this.entries.delete(key);
    this.entries.set(key, entry);
  }
}

function cacheKey(dir: string, query: string | null): string {
  return `${dir}\u0000${query === null ? '*' : `=${query}`}`;
}

/**
 * What to ask the agent for next, or `null` when the cache already answers.
 * A directory never listed is fetched unfiltered first; only a truncated one
 * is re-asked with the fragment as `q`.
 */
export function nextListingRequest(
  cache: DirListingCache,
  query: PathQuery,
): { dir: string; q: string | null } | null {
  const hit = cache.lookup(query.dir, query.fragment);
  if (hit?.complete === true) return null;
  if (!cache.hasUnfiltered(query.dir)) return { dir: query.dir, q: null };
  if (query.fragment === '') return null;
  return { dir: query.dir, q: query.fragment };
}

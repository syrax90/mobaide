/**
 * Deduplicate a refresh across concurrent callers *and* across browser tabs.
 *
 * Two layers, because they solve different problems:
 *
 *  - A module-level in-flight promise collapses concurrent callers inside one
 *    tab. This is the direct analogue of Android's
 *    `AtomicReference<Deferred<RefreshResult>>` and iOS's actor-held
 *    `inFlightRefresh` task.
 *  - `navigator.locks` serializes across tabs. That matters here in a way it
 *    does not on mobile: refresh tokens rotate on use, so two tabs refreshing
 *    at once would have one of them present an already-consumed token and get
 *    a 401. The lock makes the second tab wait, and the `recheck` hook below
 *    lets it discover that the first tab already produced a usable token and
 *    skip the network entirely.
 *
 * Web Locks is unavailable in non-secure contexts and in some embedded
 * webviews; there the same-tab layer still applies and cross-tab refreshes
 * merely race, which the 401-then-reauthenticate path already handles.
 */
const inFlight = new Map<string, Promise<unknown>>();

/** The Web Lock a given single-flight key serializes on. */
export function lockNameFor(key: string): string {
  return `mobaide-${key}`;
}

/** The user-session key, shared by rotation, boot restore and sign-out. */
export const USER_REFRESH_KEY = 'user-refresh';

export interface SingleFlightOptions<T> {
  /**
   * Runs while holding the cross-tab lock, before `task`. Returning a non-null
   * value short-circuits: another tab already did the work. Use it to re-read
   * shared storage that the winning tab just wrote.
   */
  recheck?: () => T | null;
  /**
   * Bounds the *wait* for the lock, which `task`'s own deadline cannot: an
   * `AbortSignal.timeout` created inside `task` only starts once the lock is
   * held, so without this the queue behind a slow holder is unbounded. Web
   * Locks rejects the request with `AbortError` before granting, and ignores
   * the signal afterwards, so this can never interrupt work already in flight.
   */
  signal?: AbortSignal;
}

export async function singleFlight<T>(
  key: string,
  task: () => Promise<T>,
  options: SingleFlightOptions<T> = {},
): Promise<T> {
  const existing = inFlight.get(key);
  if (existing !== undefined) {
    return existing as Promise<T>;
  }

  const run = async (): Promise<T> => {
    const guarded = async (): Promise<T> => {
      const fresh = options.recheck?.() ?? null;
      return fresh !== null ? fresh : await task();
    };
    if (typeof navigator !== 'undefined' && navigator.locks !== undefined) {
      const name = lockNameFor(key);
      return options.signal === undefined
        ? await navigator.locks.request(name, guarded)
        : await navigator.locks.request(name, { signal: options.signal }, guarded);
    }
    return await guarded();
  };

  // Store the promise before awaiting so a caller that arrives in the same
  // microtask sees it. `finally` clears the slot whether the task resolved or
  // threw, so a failed refresh does not wedge every later attempt.
  const promise = run().finally(() => {
    inFlight.delete(key);
  });
  inFlight.set(key, promise);
  return promise;
}

/** Test seam — drops any in-flight entries left by a previous test. */
export function resetSingleFlight(): void {
  inFlight.clear();
}

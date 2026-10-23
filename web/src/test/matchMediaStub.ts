import { vi } from 'vitest';

// Kept in step with `useResolvedTheme.ts`, the only production reader.
const DARK_QUERY = '(prefers-color-scheme: dark)';

/**
 * Install a controllable stand-in for `matchMedia`, for suites that exercise
 * `(prefers-color-scheme: dark)`.
 *
 * `src/test/setup.ts` already defines a `matchMedia`, but it is a constant
 * `matches: false` whose listeners go nowhere, so any suite that needs a dark OS
 * or a live OS flip has to replace it outright — this is the one, rather than a
 * copy per suite. The same object answers every call for the dark query, so
 * `set` fires the listener the hook under test actually registered.
 *
 * Only `(prefers-color-scheme: dark)` is answered; every other query reports
 * non-matching. Without that guard `stubPrefersDark(true)` would also make
 * `(prefers-reduced-motion: reduce)` and `(display-mode: standalone)` match, and
 * a suite rendering `AgentCredentialsPanel` or anything reading `browserEnv`
 * would take the wrong branch with nothing in its assertions to show it.
 *
 * Never undone with `vi.unstubAllGlobals`: that would take the localStorage
 * stand-in from `localStorageStub` with it. Suites that need a clean slate
 * re-stub instead.
 *
 * `set` deliberately leaves `act()` to the caller, so the React boundary stays
 * visible at the point of the state change.
 */
export function stubPrefersDark(initial: boolean): { set: (next: boolean) => void } {
  return stubMediaQuery(DARK_QUERY, initial);
}

/**
 * The same stand-in for any one media query: only `query` is answered, every
 * other query reports non-matching.
 */
export function stubMediaQuery(
  query: string,
  initial: boolean,
): { set: (next: boolean) => void } {
  const listeners = new Set<() => void>();
  const media = {
    matches: initial,
    addEventListener: (_type: string, listener: () => void) => {
      listeners.add(listener);
    },
    removeEventListener: (_type: string, listener: () => void) => {
      listeners.delete(listener);
    },
  };
  const inert = {
    matches: false,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  };
  vi.stubGlobal(
    'matchMedia',
    (asked: string) => (asked === query ? media : inert) as unknown as MediaQueryList,
  );
  return {
    set: (next: boolean) => {
      media.matches = next;
      listeners.forEach((listener) => {
        listener();
      });
    },
  };
}

/**
 * A stand-in for `window` / `document` that lets a test fire the listeners.
 *
 * Shared because jsdom will not let a test set `document.hidden` — it is a
 * read-only getter derived from `visibilityState` — so every visibility-driven
 * module takes its event target by injection and every one of their tests needs
 * the same double.
 */
export interface FakeEventTarget {
  hidden: boolean;
  addEventListener: (type: string, listener: () => void) => void;
  removeEventListener: (type: string, listener: () => void) => void;
  /** Dispatch to everything registered for `type`. */
  fire: (type: string) => void;
  /** How many listeners are registered for `type`. */
  count: (type: string) => number;
}

export function fakeEventTarget(): FakeEventTarget {
  const listeners = new Map<string, Set<() => void>>();
  return {
    hidden: false,
    addEventListener: (type, listener) => {
      const set = listeners.get(type) ?? new Set();
      set.add(listener);
      listeners.set(type, set);
    },
    removeEventListener: (type, listener) => {
      listeners.get(type)?.delete(listener);
    },
    fire: (type) => {
      for (const listener of [...(listeners.get(type) ?? [])]) listener();
    },
    count: (type) => listeners.get(type)?.size ?? 0,
  };
}

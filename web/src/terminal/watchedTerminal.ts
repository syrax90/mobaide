/**
 * Which terminal is on screen, and who wants to know.
 *
 * `terminalStore` owns the models and already imports `sessionModel`, so a
 * model cannot read the pointer from there without closing an import cycle.
 * Same split, and the same reasoning, as `terminalEviction`: a tiny module with
 * **no imports**, which both sides may depend on.
 *
 * The pointer is a single key rather than a set because the route renders one
 * terminal at a time. A model that is retained but off screen is as unwatched
 * as one in a hidden tab — `MAX_RETAINED` keeps three attached while at most
 * one is being read.
 */
export type MountedListener = (key: string | null) => void;

let mounted: string | null = null;
const listeners = new Set<MountedListener>();

function publish(): void {
  for (const listener of [...listeners]) listener(mounted);
}

export function setMountedTerminal(key: string): void {
  if (mounted === key) return;
  mounted = key;
  publish();
}

/**
 * Clears only if `key` is still the mounted one — a route switch mounts the
 * next terminal before the previous one's cleanup runs.
 */
export function clearMountedTerminal(key: string): void {
  if (mounted !== key) return;
  mounted = null;
  publish();
}

export function mountedTerminal(): string | null {
  return mounted;
}

/** Subscribe to mount changes. Returns the unsubscriber. */
export function onMountedTerminal(listener: MountedListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

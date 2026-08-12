/**
 * Whether this tab is on screen, published to whoever is spending bandwidth.
 *
 * `resumeOnFocus` also listens for `visibilitychange`, but it cannot carry this:
 * its verb is `forceReconnectAllEvents()`, and `forceReconnect()` early-returns
 * once `shouldReconnect` is false — which is exactly the state a deliberately
 * suspended channel is left in, so a terminal routed through that seam would
 * never come back. Its threshold also means something different ("hidden long
 * enough that the socket may be half-open"), and it is imported from the eager
 * bundle, where anything that reaches a terminal model would drag `@xterm/xterm`
 * into the initial chunk.
 *
 * Hence a separate module with **no imports**, like `liveChannels` and
 * `terminalEviction`. Two listeners on one event is not a cost worth
 * engineering away.
 *
 * Subscribers are module-scoped and deliberately **outlive installation**:
 * StrictMode runs the installing effect as install → uninstall → install, and a
 * subscriber registered by a model in between must survive that.
 */
export type VisibilityListener = (hidden: boolean) => void;

/** The slice of `document` this needs, narrow enough for a plain test double. */
export interface VisibilityDoc {
  readonly hidden: boolean;
  addEventListener: (type: string, listener: () => void) => void;
  removeEventListener: (type: string, listener: () => void) => void;
}

const listeners = new Set<VisibilityListener>();
let hidden = false;

/** Whether the tab is currently hidden. False before anything is installed. */
export function tabHidden(): boolean {
  return hidden;
}

/** Subscribe to hidden/visible transitions. Returns the unsubscriber. */
export function onTabVisibility(listener: VisibilityListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * Install the listener. Returns the uninstaller.
 *
 * The uninstaller restores "visible", so a torn-down installation cannot strand
 * subscribers believing they are in a hidden tab — and a test file cannot leak
 * a hidden document into the next case.
 */
export function observeTabVisibility(doc: VisibilityDoc = document): () => void {
  const apply = (next: boolean): void => {
    if (hidden === next) return;
    hidden = next;
    for (const listener of [...listeners]) listener(next);
  };

  const onVisibility = (): void => {
    apply(doc.hidden);
  };

  apply(doc.hidden);
  doc.addEventListener('visibilitychange', onVisibility);
  return () => {
    doc.removeEventListener('visibilitychange', onVisibility);
    apply(false);
  };
}

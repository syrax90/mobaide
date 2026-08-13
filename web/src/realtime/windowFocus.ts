/**
 * Whether this window holds OS focus, published to whoever must notice the
 * user coming back.
 *
 * `tabVisibility` cannot carry this: `visibilitychange` never fires when the
 * user switches between side-by-side windows — the tab stays visible while
 * focus moves away — so for "the user is now using this client" the focus
 * edge is load-bearing, not belt-and-suspenders.
 *
 * Same design as `tabVisibility`: a module with no imports, and subscribers
 * that are module-scoped and deliberately **outlive installation** —
 * StrictMode runs the installing effect as install → uninstall → install, and
 * a subscriber registered by a model in between must survive that.
 */
export type FocusListener = (focused: boolean) => void;

/**
 * The slice of the browser this needs, narrow enough for a plain test double.
 * `focus`/`blur` fire on `window`, but only `document` answers `hasFocus()`.
 */
export interface FocusWin {
  hasFocus: () => boolean;
  addEventListener: (type: string, listener: () => void) => void;
  removeEventListener: (type: string, listener: () => void) => void;
}

const listeners = new Set<FocusListener>();
let focused = true;

/** Whether the window currently holds focus. True before anything is installed. */
export function windowFocused(): boolean {
  return focused;
}

/** Subscribe to focus/blur transitions. Returns the unsubscriber. */
export function onWindowFocus(listener: FocusListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * Install the listeners. Returns the uninstaller.
 *
 * The uninstaller restores "focused", so a torn-down installation cannot
 * strand subscribers believing the window is blurred — and a test file cannot
 * leak a blurred window into the next case.
 */
export function observeWindowFocus(win: FocusWin = browserFocusWin()): () => void {
  const apply = (next: boolean): void => {
    if (focused === next) return;
    focused = next;
    for (const listener of [...listeners]) listener(next);
  };

  const onFocus = (): void => {
    apply(true);
  };
  const onBlur = (): void => {
    apply(false);
  };

  apply(win.hasFocus());
  win.addEventListener('focus', onFocus);
  win.addEventListener('blur', onBlur);
  return () => {
    win.removeEventListener('focus', onFocus);
    win.removeEventListener('blur', onBlur);
    apply(true);
  };
}

function browserFocusWin(): FocusWin {
  return {
    hasFocus: () => document.hasFocus(),
    addEventListener: (type, listener) => window.addEventListener(type, listener),
    removeEventListener: (type, listener) => window.removeEventListener(type, listener),
  };
}

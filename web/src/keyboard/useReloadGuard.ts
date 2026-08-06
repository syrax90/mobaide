/**
 * Stop the browser reloading out from under an attached terminal.
 *
 * A reload costs the session's whole replay and anything typed into the
 * composer, and Ctrl+R is a chord people press in a terminal on purpose
 * (readline's reverse history search).
 *
 * **Capture phase, and `preventDefault` only.** Capture runs before xterm's own
 * handler, and `preventDefault()` does not stop propagation — so when the
 * terminal has focus xterm still encodes Ctrl+R to `0x12` and sends it, exactly
 * as if this guard were not here. What the guard adds is the case xterm cannot
 * cover: focus parked on the composer, a bar button, or anywhere else on the
 * page, where the keystroke would otherwise reach the browser.
 */
import { useEffect } from 'react';

const RELOAD_CODES = new Set(['KeyR', 'F5']);

export function useReloadGuard(active: boolean): void {
  useEffect(() => {
    if (!active) return undefined;

    const onKeyDown = (event: KeyboardEvent): void => {
      if (!RELOAD_CODES.has(event.code) || event.altKey) return;
      // F5 reloads bare; Ctrl+R / Cmd+R (and their Shift hard-reload forms) need
      // the primary modifier, or every literal `r` typed into the terminal would
      // be swallowed.
      const needsModifier = event.code === 'KeyR';
      if (needsModifier && !(event.ctrlKey || event.metaKey)) return;
      event.preventDefault();
    };

    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
    };
  }, [active]);
}

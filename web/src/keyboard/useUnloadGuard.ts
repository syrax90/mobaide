/**
 * Ask before the tab closes on a live, running session.
 *
 * Two limits worth stating rather than hiding. `beforeunload` only produces a
 * prompt once the page has **sticky activation** — a browser will silently
 * ignore it if the user has not interacted with the page at all — so a tab
 * opened by a deep link and never clicked closes without asking. And in Firefox
 * on Windows and Linux, Ctrl+W is unsuppressable
 * (https://bugzilla.mozilla.org/show_bug.cgi?id=1291706): the prompt is the only
 * thing standing between that chord and a closed tab, which is most of why this
 * exists.
 *
 * Registered only while the session is live *and* running, so reading a finished
 * transcript never nags.
 */
import { useEffect } from 'react';

export function useUnloadGuard(active: boolean): void {
  useEffect(() => {
    if (!active) return undefined;

    const onBeforeUnload = (event: BeforeUnloadEvent): void => {
      event.preventDefault();
      // Legacy path for Chrome/Edge before 119. Any *truthy* value arms it —
      // `returnValue` is initialised to the empty string, so assigning `''` is
      // the neutral value and would arm nothing. The value is never displayed;
      // every current browser renders its own wording.
      event.returnValue = true;
    };

    window.addEventListener('beforeunload', onBeforeUnload);
    return () => {
      window.removeEventListener('beforeunload', onBeforeUnload);
    };
  }, [active]);
}

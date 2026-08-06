/**
 * Route app-owned chords (`appShortcuts.ts`) to the screen, from anywhere on the
 * page.
 *
 * This has to be a document-level listener, not xterm's
 * `attachCustomKeyEventHandler`: xterm registers its `keydown` on the terminal's
 * own `textarea` and nowhere else, so a handler hung off it fires only while the
 * grid has focus. Focus sitting in the composer, on a shortcut-bar button, on
 * the Commands button, or on `document.body` — which is exactly where it lands
 * after a dialog closes — would leave the chord dead, and on Firefox for Windows
 * and Linux Ctrl+Shift+P would open a private window instead.
 *
 * Capture phase, so it runs before xterm's textarea listener. `stopPropagation`
 * keeps the chord away from xterm and from every React `onKeyDown` (React
 * delegates to the root container) — but it does **not** silence other listeners
 * on `document` in the same phase, which is what `enabled` is for.
 *
 * `enabled` exists because the chord recorder is also a document-capture
 * listener: while it is open the app has to release its own chord, or pressing
 * Ctrl+Shift+P would open the palette *and* record `ctrl+shift+p` at the same
 * time — and that chord is precisely the one the "shadows an app shortcut"
 * warning exists to explain.
 */
import { useEffect, useMemo } from 'react';
import { currentKeyboardEnv } from './browserEnv';
import { matchAppShortcut, type AppCommand } from './appShortcuts';

export function useAppShortcuts(
  onCommand: (command: AppCommand) => void,
  enabled = true,
): void {
  // Read once per mount rather than per keystroke: the OS never changes for the
  // life of the page, and `currentKeyboardEnv` costs two `matchMedia` queries.
  const env = useMemo(currentKeyboardEnv, []);

  useEffect(() => {
    if (!enabled) return undefined;

    const onKeyDown = (event: KeyboardEvent): void => {
      const command = matchAppShortcut(event, env);
      if (command === null) return;
      event.preventDefault();
      event.stopPropagation();
      onCommand(command);
    };

    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
    };
  }, [env, onCommand, enabled]);
}

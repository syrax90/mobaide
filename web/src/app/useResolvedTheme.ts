import { useEffect, useState } from 'react';
import { resolveTheme, useThemeStore } from '../stores/themeStore';

const DARK_QUERY = '(prefers-color-scheme: dark)';

function systemPrefersDark(): boolean {
  // jsdom ships no `matchMedia` at all, so an unguarded read takes down every
  // component test that renders a screen using this hook. Same shape as
  // `isStandaloneDisplay` in keyboard/browserEnv.ts.
  if (typeof window.matchMedia !== 'function') return false;
  return window.matchMedia(DARK_QUERY).matches;
}

/**
 * The theme actually painted, as opposed to the stored preference.
 *
 * `ThemeProvider` stamps this onto `data-theme` for the CSS to read. Anything
 * that has to hand a concrete theme to a third-party SDK needs the same value
 * in JS — the Google Identity Services button picks its `theme` this way,
 * because it renders into a cross-origin frame that our CSS cannot reach.
 *
 * The media listener is attached whatever the preference, so that switching
 * back to `system` resolves against the OS value current at that moment.
 */
export function useResolvedTheme(): 'light' | 'dark' {
  const preference = useThemeStore((state) => state.preference);
  const [prefersDark, setPrefersDark] = useState(systemPrefersDark);

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const media = window.matchMedia(DARK_QUERY);
    const apply = () => {
      setPrefersDark(media.matches);
    };
    apply();
    media.addEventListener('change', apply);
    return () => {
      media.removeEventListener('change', apply);
    };
  }, []);

  return resolveTheme(preference, prefersDark);
}

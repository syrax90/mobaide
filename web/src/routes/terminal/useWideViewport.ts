import { useEffect, useState } from 'react';

/** The layout breakpoint `global.css` uses for the terminal chrome. */
export const WIDE_VIEWPORT_QUERY = '(min-width: 901px)';

function matches(): boolean {
  // jsdom ships no `matchMedia`; the stub in `test/setup.ts` answers false.
  if (typeof window.matchMedia !== 'function') return false;
  return window.matchMedia(WIDE_VIEWPORT_QUERY).matches;
}

/** Whether the viewport is wide enough to dock a side panel, tracked live. */
export function useWideViewport(): boolean {
  const [wide, setWide] = useState(matches);

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const media = window.matchMedia(WIDE_VIEWPORT_QUERY);
    const apply = () => {
      setWide(media.matches);
    };
    apply();
    media.addEventListener('change', apply);
    return () => {
      media.removeEventListener('change', apply);
    };
  }, []);

  return wide;
}

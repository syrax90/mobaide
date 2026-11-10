/**
 * Plain full screen for the chat view: the page fills the screen, the browser
 * keeps its keys (Esc leaves). The terminal's immersive mode adds keyboard
 * capture on top (`keyboard/useKeyboardLock.ts`); a chat has no use for it.
 * Full screen outlives the view on purpose, so switching sessions keeps it;
 * `active` starts from the document's real state for the same reason.
 *
 * Not offered where the page cannot go full screen: iPhone Safari has no
 * element full screen, and an iframe without `allowfullscreen` refuses it.
 */
import { useCallback, useEffect, useState } from 'react';

export interface FullscreenControls {
  supported: boolean;
  active: boolean;
  error: string | null;
  enter: () => void;
  exit: () => void;
}

function fullscreenSupported(): boolean {
  return (
    typeof document !== 'undefined' &&
    document.fullscreenEnabled &&
    typeof document.documentElement.requestFullscreen === 'function'
  );
}

export function useFullscreen(): FullscreenControls {
  const supported = fullscreenSupported();
  const [active, setActive] = useState(
    () => typeof document !== 'undefined' && document.fullscreenElement !== null,
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!supported) return undefined;
    const onChange = (): void => {
      setActive(document.fullscreenElement !== null);
    };
    document.addEventListener('fullscreenchange', onChange);
    return () => {
      document.removeEventListener('fullscreenchange', onChange);
    };
  }, [supported]);

  const enter = useCallback(() => {
    setError(null);
    void document.documentElement.requestFullscreen().catch(() => {
      setError('Your browser did not allow full screen.');
    });
  }, []);

  const exit = useCallback(() => {
    if (document.fullscreenElement !== null) {
      void document.exitFullscreen().catch(() => undefined);
    }
  }, []);

  return { supported, active, error, enter, exit };
}

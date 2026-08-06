/**
 * Immersive mode — fullscreen plus the Keyboard Lock API, the only way a web
 * page can receive Ctrl+W, Ctrl+T, Ctrl+N and F11 at all.
 *
 * Chromium-only and feature-detected: `navigator.keyboard` does not exist in
 * Firefox or Safari, and the toggle simply is not rendered there. The API is
 * available only while JavaScript-initiated fullscreen is active
 * (https://developer.chrome.com/docs/capabilities/web-apis/keyboard-lock).
 *
 * Chrome announced a permission prompt for this in 131 and then **did not ship
 * it** — the experiment showed no benefit on scam sites and caused confusion on
 * legitimate ones (https://developer.chrome.com/blog/keyboard-lock-pointer-lock-permission).
 * So there is no prompt to design around; the `NotAllowedError` branch below
 * still matters for every other refusal.
 *
 * `lock()` runs **before** `requestFullscreen()`, which is the WICG
 * recommendation. That ordering would be a liability if a permission prompt did
 * appear — awaiting it would likely consume the transient user activation
 * `requestFullscreen()` needs — which is exactly why the paragraph above is
 * worth keeping accurate.
 *
 * With no argument every key is captured, Escape included — leaving fullscreen
 * then needs a press-and-hold, which is the documented Chrome behaviour and the
 * whole point: an Esc that exits the browser chrome is an Esc the TUI never got.
 */
import { useCallback, useEffect, useState } from 'react';

interface KeyboardLockApi {
  lock: (keyCodes?: string[]) => Promise<void>;
  unlock: () => void;
}

function keyboardApi(): KeyboardLockApi | null {
  if (typeof navigator === 'undefined') return null;
  const candidate = (navigator as Navigator & { keyboard?: KeyboardLockApi }).keyboard;
  return candidate !== undefined && typeof candidate.lock === 'function' ? candidate : null;
}

export function keyboardLockSupported(): boolean {
  return keyboardApi() !== null && typeof document !== 'undefined';
}

export interface KeyboardLockControls {
  supported: boolean;
  active: boolean;
  error: string | null;
  enter: () => void;
  exit: () => void;
}

export function useKeyboardLock(): KeyboardLockControls {
  const [active, setActive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const supported = keyboardLockSupported();

  // Leaving fullscreen by any route — Esc held down, the window chrome, another
  // page — has to release the lock, or the next tab keeps eating Ctrl+W.
  useEffect(() => {
    if (!supported) return undefined;
    const onFullscreenChange = (): void => {
      if (document.fullscreenElement === null) {
        keyboardApi()?.unlock();
        setActive(false);
      }
    };
    document.addEventListener('fullscreenchange', onFullscreenChange);
    return () => {
      document.removeEventListener('fullscreenchange', onFullscreenChange);
    };
  }, [supported]);

  useEffect(
    () => () => {
      keyboardApi()?.unlock();
    },
    [],
  );

  const enter = useCallback(() => {
    const api = keyboardApi();
    if (api === null) return;
    setError(null);
    void api
      .lock()
      .then(() => document.documentElement.requestFullscreen())
      .then(() => {
        setActive(true);
      })
      .catch((cause: unknown) => {
        api.unlock();
        setActive(false);
        setError(
          cause instanceof Error && cause.name === 'NotAllowedError'
            ? 'Your browser refused keyboard capture for this page.'
            : 'Could not enter immersive mode.',
        );
      });
  }, []);

  const exit = useCallback(() => {
    keyboardApi()?.unlock();
    setActive(false);
    if (document.fullscreenElement !== null) {
      void document.exitFullscreen().catch(() => undefined);
    }
  }, []);

  return { supported, active, error, enter, exit };
}

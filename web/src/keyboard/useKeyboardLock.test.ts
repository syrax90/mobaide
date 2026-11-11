import { act, cleanup, fireEvent, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { useKeyboardLock } from './useKeyboardLock';

/** Call order matters: `lock()` runs before `requestFullscreen()`, per WICG. */
let calls: string[];
let lockResult: () => Promise<void>;
let inFullscreen: boolean;

/** jsdom implements none of this; the whole API surface is a stub. */
function stubImmersiveApis(): void {
  Object.defineProperty(navigator, 'keyboard', {
    configurable: true,
    value: {
      lock: (): Promise<void> => {
        calls.push('lock');
        return lockResult();
      },
      unlock: (): void => {
        calls.push('unlock');
      },
    },
  });
  Object.defineProperty(document.documentElement, 'requestFullscreen', {
    configurable: true,
    value: (): Promise<void> => {
      calls.push('requestFullscreen');
      inFullscreen = true;
      return Promise.resolve();
    },
  });
  Object.defineProperty(document, 'exitFullscreen', {
    configurable: true,
    value: (): Promise<void> => {
      calls.push('exitFullscreen');
      inFullscreen = false;
      return Promise.resolve();
    },
  });
  Object.defineProperty(document, 'fullscreenElement', {
    configurable: true,
    get: () => (inFullscreen ? document.documentElement : null),
  });
}

const refusedWith = (name: string) => (): Promise<void> => {
  const error = new Error('refused');
  error.name = name;
  return Promise.reject(error);
};

async function enter(controls: { current: ReturnType<typeof useKeyboardLock> }): Promise<void> {
  await act(async () => {
    controls.current.enter();
    await Promise.resolve();
  });
}

beforeEach(() => {
  calls = [];
  inFullscreen = false;
  lockResult = () => Promise.resolve();
  stubImmersiveApis();
});

afterEach(() => {
  cleanup();
  Reflect.deleteProperty(navigator, 'keyboard');
  Reflect.deleteProperty(document.documentElement, 'requestFullscreen');
  Reflect.deleteProperty(document, 'exitFullscreen');
  Reflect.deleteProperty(document, 'fullscreenElement');
});

describe('useKeyboardLock', () => {
  it('locks the keyboard before asking for fullscreen', async () => {
    // The other order would be a liability if Chrome ever shipped the permission
    // prompt it announced: awaiting it would spend the activation fullscreen needs.
    const { result } = renderHook(useKeyboardLock);

    await enter(result);

    expect(calls).toEqual(['lock', 'requestFullscreen']);
    expect(result.current.active).toBe(true);
    expect(result.current.error).toBeNull();
  });

  it('releases the lock when fullscreen ends by any route', async () => {
    // Escape held down, the window chrome, another page — all of them land
    // here, and a lock left armed keeps eating Ctrl+W in the next tab.
    const { result } = renderHook(useKeyboardLock);
    await enter(result);
    calls.length = 0;
    inFullscreen = false;

    act(() => {
      fireEvent(document, new Event('fullscreenchange'));
    });

    expect(calls).toEqual(['unlock']);
    expect(result.current.active).toBe(false);
  });

  it('names the refusal when the browser turns keyboard capture down', async () => {
    lockResult = refusedWith('NotAllowedError');
    const { result } = renderHook(useKeyboardLock);

    await enter(result);

    expect(result.current.error).toBe('Your browser refused keyboard capture for this page.');
    expect(result.current.active).toBe(false);
    // Never left half-armed: the lock is released even though it never took.
    expect(calls).toEqual(['lock', 'unlock']);
  });

  it('releases the lock when the screen unmounts', async () => {
    const lock = renderHook(useKeyboardLock);
    await enter(lock.result);
    calls.length = 0;

    lock.unmount();

    expect(calls).toEqual(['unlock']);
  });

  it('reports itself unsupported where the API does not exist', () => {
    // Firefox and Safari ship no `navigator.keyboard`, and the toggle is not
    // rendered there at all.
    Reflect.deleteProperty(navigator, 'keyboard');
    const { result } = renderHook(useKeyboardLock);

    expect(result.current.supported).toBe(false);

    act(() => {
      result.current.enter();
    });

    expect(calls).toEqual([]);
  });
});

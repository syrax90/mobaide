import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubLocalStorage } from '../test/localStorageStub';

// Before importing the store, so zustand's `persist` captures the stand-in.
stubLocalStorage();

const { useResolvedTheme } = await import('./useResolvedTheme');
const { useThemeStore } = await import('../stores/themeStore');

/**
 * jsdom ships no `matchMedia` at all, so it is stubbed onto the global rather
 * than spied on. One mutable object is handed back from every call, so the
 * listener the hook registers is the one `set` fires.
 *
 * Never cleared with `vi.unstubAllGlobals`: that would take the localStorage
 * stand-in with it. Every test installs its own instead.
 */
function stubPrefersDark(initial: boolean): { set: (next: boolean) => void } {
  const listeners = new Set<() => void>();
  const media = {
    matches: initial,
    addEventListener: (_type: string, listener: () => void) => {
      listeners.add(listener);
    },
    removeEventListener: (_type: string, listener: () => void) => {
      listeners.delete(listener);
    },
  };
  vi.stubGlobal('matchMedia', () => media as unknown as MediaQueryList);
  return {
    set: (next: boolean) => {
      media.matches = next;
      listeners.forEach((listener) => {
        listener();
      });
    },
  };
}

beforeEach(() => {
  useThemeStore.setState({ preference: 'system' });
});

afterEach(() => {
  useThemeStore.setState({ preference: 'system' });
});

describe('useResolvedTheme', () => {
  it('resolves light on an engine with no matchMedia, rather than throwing', () => {
    vi.stubGlobal('matchMedia', undefined);

    const { result } = renderHook(() => useResolvedTheme());

    expect(result.current).toBe('light');
  });

  it('follows the OS while the preference is system', () => {
    stubPrefersDark(true);

    const { result } = renderHook(() => useResolvedTheme());

    expect(result.current).toBe('dark');
  });

  it('lets an explicit preference override the OS', () => {
    stubPrefersDark(true);
    useThemeStore.setState({ preference: 'light' });

    const { result } = renderHook(() => useResolvedTheme());

    expect(result.current).toBe('light');
  });

  it('tracks an OS change while on system', () => {
    const media = stubPrefersDark(false);
    const { result } = renderHook(() => useResolvedTheme());
    expect(result.current).toBe('light');

    act(() => {
      media.set(true);
    });

    expect(result.current).toBe('dark');
  });

  // The listener is attached whatever the preference, so returning to `system`
  // resolves against the OS value current at that moment.
  it('picks up the OS value when the preference returns to system', () => {
    const media = stubPrefersDark(false);
    useThemeStore.setState({ preference: 'light' });
    const { result } = renderHook(() => useResolvedTheme());

    act(() => {
      media.set(true);
    });
    expect(result.current).toBe('light');

    act(() => {
      useThemeStore.setState({ preference: 'system' });
    });

    expect(result.current).toBe('dark');
  });
});

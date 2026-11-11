import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubLocalStorage } from '../test/localStorageStub';
import { stubPrefersDark } from '../test/matchMediaStub';

// Before importing the store, so zustand's `persist` captures the stand-in.
stubLocalStorage();

const { useResolvedTheme, useSystemTheme } = await import('./useResolvedTheme');
const { useThemeStore } = await import('../stores/themeStore');

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

  it('tracks an OS change while on system', () => {
    const media = stubPrefersDark(false);
    const { result } = renderHook(() => useResolvedTheme());
    expect(result.current).toBe('light');

    act(() => {
      media.set(true);
    });

    expect(result.current).toBe('dark');
  });
});

describe('useSystemTheme', () => {
  it('reports the OS appearance and tracks it live', () => {
    const media = stubPrefersDark(false);
    const { result } = renderHook(() => useSystemTheme());
    expect(result.current).toBe('light');

    act(() => {
      media.set(true);
    });

    expect(result.current).toBe('dark');
  });
});

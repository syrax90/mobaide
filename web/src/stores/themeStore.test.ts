import { beforeEach, describe, expect, it, vi } from 'vitest';

// Node >= 22 defines an experimental global `localStorage` that is undefined
// without --localstorage-file and shadows jsdom's implementation under
// vitest. Install a functional stand-in before importing the store so
// zustand's default createJSONStorage(() => window.localStorage) captures it.
const backing = new Map<string, string>();
vi.stubGlobal('localStorage', {
  getItem: (key: string) => backing.get(key) ?? null,
  setItem: (key: string, value: string) => {
    backing.set(key, String(value));
  },
  removeItem: (key: string) => {
    backing.delete(key);
  },
  clear: () => {
    backing.clear();
  },
  key: (index: number) => [...backing.keys()][index] ?? null,
  get length() {
    return backing.size;
  },
});

const { resolveTheme, useThemeStore } = await import('./themeStore');

const STORAGE_KEY = 'mobaide-theme';

function seedStorage(envelope: unknown) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(envelope));
}

describe('themeStore hydration', () => {
  beforeEach(() => {
    // setState writes back through the persist middleware, so reset the
    // in-memory singleton first and clear storage after.
    useThemeStore.setState({ preference: 'system' });
    window.localStorage.clear();
  });

  it('restores a valid persisted preference', async () => {
    seedStorage({ state: { preference: 'dark' }, version: 0 });

    await useThemeStore.persist.rehydrate();

    expect(useThemeStore.getState().preference).toBe('dark');
  });

  it('falls back to system for an unknown persisted preference', async () => {
    seedStorage({ state: { preference: 'bogus' }, version: 0 });

    await useThemeStore.persist.rehydrate();

    expect(useThemeStore.getState().preference).toBe('system');
  });

  it('falls back to system when the envelope has no state object', async () => {
    useThemeStore.setState({ preference: 'dark' });
    seedStorage({ version: 0 });

    await useThemeStore.persist.rehydrate();

    expect(useThemeStore.getState().preference).toBe('system');
  });

  it('keeps the store usable when storage holds malformed JSON', async () => {
    window.localStorage.setItem(STORAGE_KEY, '{not json');

    await useThemeStore.persist.rehydrate();

    expect(useThemeStore.getState().preference).toBe('system');
  });
});

describe('resolveTheme', () => {
  it('follows the OS preference when set to system', () => {
    expect(resolveTheme('system', true)).toBe('dark');
    expect(resolveTheme('system', false)).toBe('light');
  });

  it('ignores the OS preference for an explicit choice', () => {
    expect(resolveTheme('dark', false)).toBe('dark');
    expect(resolveTheme('light', true)).toBe('light');
  });
});

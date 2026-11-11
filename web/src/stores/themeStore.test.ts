import { beforeEach, describe, expect, it } from 'vitest';
import { stubLocalStorage } from '../test/localStorageStub';

// Before importing the store, so zustand's
// `createJSONStorage(() => window.localStorage)` captures the stand-in.
stubLocalStorage();

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

  it('falls back to system for an unknown persisted preference', async () => {
    seedStorage({ state: { preference: 'bogus' }, version: 0 });

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
});

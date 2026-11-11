import { beforeEach, describe, expect, it } from 'vitest';
import { stubLocalStorage } from '../test/localStorageStub';

// Before importing the store, so zustand's
// `createJSONStorage(() => window.localStorage)` captures the stand-in.
stubLocalStorage();

const { useSessionsPanelStore } = await import('./sessionsPanelStore');

const STORAGE_KEY = 'mobaide-sessions-panel';

function seed(state: Record<string, unknown>) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ state, version: 0 }));
}

beforeEach(() => {
  useSessionsPanelStore.setState({ pinned: false });
  window.localStorage.clear();
});

describe('hydration', () => {
  it('restores a persisted pin', async () => {
    seed({ pinned: true });

    await useSessionsPanelStore.persist.rehydrate();

    expect(useSessionsPanelStore.getState().pinned).toBe(true);
  });

  it('treats anything but a real boolean as unpinned', async () => {
    // The same untrusted-boundary rule as `terminalPrefsStore`: a truthy
    // string hand-edited into storage must not dock a 300 px rail beside the
    // grid on every terminal visit.
    seed({ pinned: 'yes' });

    await useSessionsPanelStore.persist.rehydrate();

    expect(useSessionsPanelStore.getState().pinned).toBe(false);
  });
});

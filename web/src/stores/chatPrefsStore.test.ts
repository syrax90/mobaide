import { beforeEach, describe, expect, it } from 'vitest';
import { stubLocalStorage } from '../test/localStorageStub';

// Before importing the store, so zustand's
// `createJSONStorage(() => window.localStorage)` captures the stand-in.
stubLocalStorage();

const { useChatPrefsStore } = await import('./chatPrefsStore');

const STORAGE_KEY = 'mobaide-chat';

function seed(state: Record<string, unknown>) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ state, version: 0 }));
}

beforeEach(() => {
  useChatPrefsStore.setState({ focusView: true });
  window.localStorage.clear();
});

describe('focus view', () => {
  it('is on when nothing is stored', async () => {
    useChatPrefsStore.setState({ focusView: false });
    window.localStorage.clear();

    await useChatPrefsStore.persist.rehydrate();

    expect(useChatPrefsStore.getState().focusView).toBe(true);
  });

  it('restores a stored opt-out', async () => {
    seed({ focusView: false });

    await useChatPrefsStore.persist.rehydrate();

    expect(useChatPrefsStore.getState().focusView).toBe(false);
  });

  it('treats anything but a real false as on', async () => {
    seed({ focusView: 'false' });

    await useChatPrefsStore.persist.rehydrate();

    expect(useChatPrefsStore.getState().focusView).toBe(true);
  });

  it('stores the choice for the next visit', () => {
    useChatPrefsStore.getState().setFocusView(false);

    const stored = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? '{}') as {
      state?: { focusView?: unknown };
    };
    expect(stored.state?.focusView).toBe(false);
  });
});

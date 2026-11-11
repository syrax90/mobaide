import { beforeEach, describe, expect, it } from 'vitest';
import { stubLocalStorage } from '../test/localStorageStub';

// Before importing the store, so zustand's
// `createJSONStorage(() => window.localStorage)` captures the stand-in.
stubLocalStorage();

const { useVoicePrefsStore } = await import('./voicePrefsStore');

const STORAGE_KEY = 'mobaide-voice-prefs';

function seed(state: Record<string, unknown>) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ state, version: 0 }));
}

function stored(): Record<string, unknown> {
  const raw = window.localStorage.getItem(STORAGE_KEY);
  return raw === null ? {} : (JSON.parse(raw) as { state: Record<string, unknown> }).state;
}

beforeEach(() => {
  useVoicePrefsStore.setState({
    hiddenAgents: {},
    skipReviewAgents: {},
    cleanupNoticeSeen: false,
  });
  window.localStorage.clear();
});

describe('transcript cleanup', () => {
  it('is not kept in the browser: the agent stores it for every client', async () => {
    seed({ cleanupAgents: { a1: true }, cleanupNoticeSeen: true });

    await useVoicePrefsStore.persist.rehydrate();
    useVoicePrefsStore.getState().setMicHidden('a1', true);

    expect(useVoicePrefsStore.getState()).not.toHaveProperty('cleanupAgents');
    expect(stored()).not.toHaveProperty('cleanupAgents');
  });
});

describe('cleanup notice', () => {
  it('is unseen until acknowledged, then remembered for every agent', () => {
    expect(useVoicePrefsStore.getState().cleanupNoticeSeen).toBe(false);

    useVoicePrefsStore.getState().acknowledgeCleanupNotice();

    expect(useVoicePrefsStore.getState().cleanupNoticeSeen).toBe(true);
    expect(stored()['cleanupNoticeSeen']).toBe(true);
  });

  it.each([
    ['true', true, true],
    ['a truthy string', 'yes', false],
  ])('restores %s as seen: %s', async (_label, value, seen) => {
    // Skipping the notice skips telling the user where the text goes, so
    // only an explicit `true` counts.
    seed({ cleanupNoticeSeen: value });

    await useVoicePrefsStore.persist.rehydrate();

    expect(useVoicePrefsStore.getState().cleanupNoticeSeen).toBe(seen);
  });
});

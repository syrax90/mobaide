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
    cleanupAgents: {},
    cleanupNoticeSeen: false,
  });
  window.localStorage.clear();
});

describe('transcript cleanup', () => {
  it('is off until turned on, per agent, and persisted', () => {
    const { setCleanup } = useVoicePrefsStore.getState();

    setCleanup('a1', true);
    setCleanup('a2', true);
    setCleanup('a2', false);

    expect(useVoicePrefsStore.getState().cleanupAgents).toEqual({ a1: true });
    expect(stored()['cleanupAgents']).toEqual({ a1: true });
  });

  it('restores only real opt-ins', async () => {
    // Untrusted storage: a truthy string hand-edited in must not send text to
    // the AI agent's provider on its own.
    seed({ cleanupAgents: { a1: true, a2: 'yes', '': true, a3: 1 } });

    await useVoicePrefsStore.persist.rehydrate();

    expect(useVoicePrefsStore.getState().cleanupAgents).toEqual({ a1: true });
  });

  it('is forgotten with the agent, even when it was the agent\'s only preference', () => {
    const { setCleanup, forgetAgent } = useVoicePrefsStore.getState();
    setCleanup('a1', true);
    setCleanup('a2', true);

    forgetAgent('a1');

    expect(useVoicePrefsStore.getState().cleanupAgents).toEqual({ a2: true });
    expect(stored()['cleanupAgents']).toEqual({ a2: true });
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
    ['1', 1, false],
    ['nothing', undefined, false],
  ])('restores %s as seen: %s', async (_label, value, seen) => {
    // Skipping the notice skips telling the user where the text goes, so
    // only an explicit `true` counts.
    seed({ cleanupNoticeSeen: value });

    await useVoicePrefsStore.persist.rehydrate();

    expect(useVoicePrefsStore.getState().cleanupNoticeSeen).toBe(seen);
  });
});

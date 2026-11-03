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
  useVoicePrefsStore.setState({ hiddenAgents: {}, skipReviewAgents: {}, cleanupAgents: {} });
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
    // Anthropic on its own.
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

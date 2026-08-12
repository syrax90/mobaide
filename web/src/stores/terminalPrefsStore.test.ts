import { beforeEach, describe, expect, it } from 'vitest';
import { stubLocalStorage } from '../test/localStorageStub';

// Before importing the store, so zustand's
// `createJSONStorage(() => window.localStorage)` captures the stand-in.
stubLocalStorage();

const { useTerminalPrefsStore } = await import('./terminalPrefsStore');

const STORAGE_KEY = 'mobaide-terminal';

function seed(state: Record<string, unknown>) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ state, version: 0 }));
}

beforeEach(() => {
  useTerminalPrefsStore.setState({ passCtrlVToTerminal: false });
  window.localStorage.clear();
});

describe('hydration', () => {
  it('restores a persisted opt-in', async () => {
    seed({ passCtrlVToTerminal: true });

    await useTerminalPrefsStore.persist.rehydrate();

    expect(useTerminalPrefsStore.getState().passCtrlVToTerminal).toBe(true);
  });

  it('treats anything but a real boolean as the default', async () => {
    // The same untrusted-boundary rule as `themeStore`: a truthy string
    // hand-edited into storage must not reach the key handler as an opt-in.
    seed({ passCtrlVToTerminal: 'yes' });

    await useTerminalPrefsStore.persist.rehydrate();

    expect(useTerminalPrefsStore.getState().passCtrlVToTerminal).toBe(false);
  });

  it('drops the keys retired builds left under this storage key', async () => {
    // Pre-removal builds persisted the shortcut bar's state and per-kind
    // recent chords in the same envelope. They must neither crash rehydrate
    // nor leak into the state object.
    seed({
      passCtrlVToTerminal: true,
      shortcutBarExpanded: false,
      recentChords: { claude: ['ctrl+a'] },
    });

    await useTerminalPrefsStore.persist.rehydrate();

    const state = useTerminalPrefsStore.getState();
    expect(state.passCtrlVToTerminal).toBe(true);
    expect('shortcutBarExpanded' in state).toBe(false);
    expect('recentChords' in state).toBe(false);
  });
});

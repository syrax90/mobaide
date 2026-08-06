import { beforeEach, describe, expect, it } from 'vitest';
import { stubLocalStorage } from '../test/localStorageStub';

// Before importing the store, so zustand's
// `createJSONStorage(() => window.localStorage)` captures the stand-in.
stubLocalStorage();

const { MAX_RECENT_CHORDS, recentChordsForKind, useTerminalPrefsStore } = await import(
  './terminalPrefsStore'
);

const STORAGE_KEY = 'mobaide-terminal';

/**
 * Seed raw JSON rather than an object literal: `{ __proto__: … }` in source
 * sets the prototype instead of creating the own property that a hand-edited
 * localStorage entry — the case the guard exists for — actually carries.
 */
function seedRaw(json: string) {
  window.localStorage.setItem(STORAGE_KEY, json);
}

function seed(state: Record<string, unknown>) {
  seedRaw(JSON.stringify({ state, version: 0 }));
}

beforeEach(() => {
  useTerminalPrefsStore.setState({
    passCtrlVToTerminal: false,
    shortcutBarExpanded: true,
    recentChords: {},
  });
  window.localStorage.clear();
});

describe('noteRecentChord', () => {
  it('records a chord under its kind, newest first', () => {
    const { noteRecentChord } = useTerminalPrefsStore.getState();

    noteRecentChord('claude', 'ctrl+a');
    noteRecentChord('claude', 'ctrl+b');

    expect(useTerminalPrefsStore.getState().recentChords.claude).toEqual(['ctrl+b', 'ctrl+a']);
  });

  it('moves a repeat to the front instead of listing it twice', () => {
    const { noteRecentChord } = useTerminalPrefsStore.getState();

    noteRecentChord('claude', 'ctrl+a');
    noteRecentChord('claude', 'ctrl+b');
    noteRecentChord('claude', 'ctrl+a');

    expect(useTerminalPrefsStore.getState().recentChords.claude).toEqual(['ctrl+a', 'ctrl+b']);
  });

  it('keeps each kind in its own bucket', () => {
    const { noteRecentChord } = useTerminalPrefsStore.getState();

    noteRecentChord('claude', 'ctrl+a');
    noteRecentChord('codex', 'ctrl+b');

    expect(useTerminalPrefsStore.getState().recentChords).toEqual({
      claude: ['ctrl+a'],
      codex: ['ctrl+b'],
    });
  });

  it('drops the oldest chord past the cap', () => {
    const { noteRecentChord } = useTerminalPrefsStore.getState();

    for (let i = 0; i < MAX_RECENT_CHORDS + 3; i += 1) {
      noteRecentChord('claude', `ctrl+${String.fromCharCode(0x61 + i)}`);
    }

    const recents = useTerminalPrefsStore.getState().recentChords.claude ?? [];
    expect(recents).toHaveLength(MAX_RECENT_CHORDS);
    expect(recents[0]).toBe('ctrl+m');
    expect(recents).not.toContain('ctrl+a');
  });

  it('refuses a token the encoder could not use', () => {
    // Recents feed the same encode path as the bar; an unparseable entry would
    // become a button that sends nothing.
    useTerminalPrefsStore.getState().noteRecentChord('claude', 'ctrl+alt+shift+a');

    expect(useTerminalPrefsStore.getState().recentChords).toEqual({});
  });

  it('refuses __proto__ as a session kind', () => {
    // A kind is an open wire string matching `^[a-z0-9_]{1,32}$`, and the store
    // is a plain object — assigning to `recents['__proto__']` would move the
    // object's prototype rather than add a bucket.
    const before = Object.getPrototypeOf(useTerminalPrefsStore.getState().recentChords) as unknown;

    useTerminalPrefsStore.getState().noteRecentChord('__proto__', 'ctrl+a');

    const after = useTerminalPrefsStore.getState().recentChords;
    expect(Object.getPrototypeOf(after)).toBe(before);
    expect(after).toEqual({});
  });
});

describe('recentChordsForKind', () => {
  it('returns the kind bucket', () => {
    expect(recentChordsForKind({ claude: ['ctrl+a'] }, 'claude')).toEqual(['ctrl+a']);
  });

  it('returns nothing while the kind is unresolved', () => {
    expect(recentChordsForKind({ claude: ['ctrl+a'] }, null)).toEqual([]);
  });

  it('does not hand back an inherited member for a kind named after one', () => {
    // `constructor` is a legal session kind, and a bare `recents[kind]` lookup
    // answers with `Object.prototype.constructor` — a function the picker would
    // then try to render as a list of tokens.
    expect(recentChordsForKind({}, 'constructor')).toEqual([]);
    expect(recentChordsForKind({}, 'toString')).toEqual([]);
  });
});

describe('hydration', () => {
  it('restores persisted recents', async () => {
    seed({ recentChords: { claude: ['ctrl+a', 'ctrl+b'] } });

    await useTerminalPrefsStore.persist.rehydrate();

    expect(useTerminalPrefsStore.getState().recentChords).toEqual({
      claude: ['ctrl+a', 'ctrl+b'],
    });
  });

  it('drops an own __proto__ key rather than moving the object prototype', async () => {
    seedRaw('{"state":{"recentChords":{"__proto__":["ctrl+a"],"claude":["ctrl+b"]}},"version":0}');

    await useTerminalPrefsStore.persist.rehydrate();

    const recents = useTerminalPrefsStore.getState().recentChords;
    expect(Object.getPrototypeOf(recents)).toBe(Object.prototype);
    expect(Object.hasOwn(recents, '__proto__')).toBe(false);
    expect(recents).toEqual({ claude: ['ctrl+b'] });
  });

  it('drops tokens a newer build wrote and this one cannot parse', async () => {
    seed({ recentChords: { claude: ['ctrl+a', 'hyper+z', 'ctrl+b'] } });

    await useTerminalPrefsStore.persist.rehydrate();

    expect(useTerminalPrefsStore.getState().recentChords.claude).toEqual(['ctrl+a', 'ctrl+b']);
  });

  it('drops a bucket whose entries are all unusable', async () => {
    seed({ recentChords: { claude: ['nonsense'], codex: ['ctrl+b'] } });

    await useTerminalPrefsStore.persist.rehydrate();

    expect(useTerminalPrefsStore.getState().recentChords).toEqual({ codex: ['ctrl+b'] });
  });

  it('drops anything that is not an array of strings', async () => {
    seed({ recentChords: { claude: 'ctrl+a', codex: [1, 'ctrl+b', null] } });

    await useTerminalPrefsStore.persist.rehydrate();

    expect(useTerminalPrefsStore.getState().recentChords).toEqual({ codex: ['ctrl+b'] });
  });

  it('truncates an over-long persisted bucket to the cap', async () => {
    const tokens = Array.from({ length: 25 }, (_, i) => `ctrl+${String(i % 10)}`);
    seed({ recentChords: { claude: tokens } });

    await useTerminalPrefsStore.persist.rehydrate();

    expect(useTerminalPrefsStore.getState().recentChords.claude).toHaveLength(MAX_RECENT_CHORDS);
  });

  it('survives recentChords being a value it was never meant to hold', async () => {
    seed({ recentChords: 'ctrl+a' });

    await useTerminalPrefsStore.persist.rehydrate();

    expect(useTerminalPrefsStore.getState().recentChords).toEqual({});
  });

  it('keeps the shortcut bar open unless it was explicitly closed', async () => {
    // Expanded by default, so an envelope written before the key existed — or a
    // truthy string hand-edited into storage — must not collapse the bar.
    seed({ recentChords: {} });
    await useTerminalPrefsStore.persist.rehydrate();
    expect(useTerminalPrefsStore.getState().shortcutBarExpanded).toBe(true);

    seed({ shortcutBarExpanded: 'no' });
    await useTerminalPrefsStore.persist.rehydrate();
    expect(useTerminalPrefsStore.getState().shortcutBarExpanded).toBe(true);

    seed({ shortcutBarExpanded: false });
    await useTerminalPrefsStore.persist.rehydrate();
    expect(useTerminalPrefsStore.getState().shortcutBarExpanded).toBe(false);
  });
});

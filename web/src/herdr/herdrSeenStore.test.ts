import { beforeEach, describe, expect, it } from 'vitest';
import { stubLocalStorage } from '../test/localStorageStub';

// Before importing the store, which persists.
stubLocalStorage();

const { useHerdrSeenStore } = await import('./herdrSeenStore');

const STORAGE_KEY = 'mobaide-herdr-seen';

function seed(state: Record<string, unknown>) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ state, version: 0 }));
}

function stored(): Record<string, number> {
  const raw = window.localStorage.getItem(STORAGE_KEY);
  return raw === null ? {} : (JSON.parse(raw) as { state: { seen: Record<string, number> } }).state.seen;
}

beforeEach(() => {
  useHerdrSeenStore.setState({ seen: {} });
  window.localStorage.clear();
});

describe('markSeen', () => {
  it('keeps the newest turn of a pane across a reload', async () => {
    // A reload must not turn every finished agent back into "Done".
    useHerdrSeenStore.getState().markSeen('pane-a', 3);
    useHerdrSeenStore.getState().markSeen('pane-a', 4);
    useHerdrSeenStore.getState().markSeen('pane-b', 1);
    const saved = stored();
    expect(saved).toEqual({ 'pane-a': 4, 'pane-b': 1 });

    // A new page: empty memory, the same storage.
    useHerdrSeenStore.setState({ seen: {} });
    seed({ seen: saved });
    await useHerdrSeenStore.persist.rehydrate();

    expect(useHerdrSeenStore.getState().seen).toEqual({ 'pane-a': 4, 'pane-b': 1 });
  });

  it('drops the oldest panes past the cap and keeps the one just seen', () => {
    const seen: Record<string, number> = {};
    for (let index = 0; index < 500; index += 1) seen[`pane-${String(index)}`] = index;
    useHerdrSeenStore.setState({ seen });

    useHerdrSeenStore.getState().markSeen('pane-new', 7);

    const after = useHerdrSeenStore.getState().seen;
    expect(Object.keys(after)).toHaveLength(500);
    expect(after['pane-new']).toBe(7);
    expect(after['pane-0']).toBeUndefined();
    expect(after['pane-1']).toBe(1);
  });
});

describe('hydration', () => {
  it('keeps only real numbers out of storage', async () => {
    // The same untrusted-boundary rule as `terminalPrefsStore`.
    seed({ seen: { ok: 5, text: '5', nan: null, nested: { turn: 1 } }, markSeen: 'not a function' });

    await useHerdrSeenStore.persist.rehydrate();

    expect(useHerdrSeenStore.getState().seen).toEqual({ ok: 5 });
    expect(typeof useHerdrSeenStore.getState().markSeen).toBe('function');
  });

  it.each([{ seen: 'broken' }, { seen: null }, {}])(
    'starts empty from a shape it does not know: %j',
    async (state) => {
      seed(state);

      await useHerdrSeenStore.persist.rehydrate();

      expect(useHerdrSeenStore.getState().seen).toEqual({});
    },
  );
});

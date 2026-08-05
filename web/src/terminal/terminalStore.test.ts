import { afterEach, describe, expect, it, vi } from 'vitest';

// `acquireTerminal` builds real models, and a real model opens a real
// `SessionIoChannel`. A token that never resolves to a value keeps `buildUrl`
// from producing a URL, so no socket is ever constructed and nothing reaches
// the network — the store's bookkeeping is what these tests are about.
vi.mock('../auth/agentTokens', () => ({
  ensureAgentToken: () => Promise.resolve(null),
  clearAgentToken: () => undefined,
}));

const { sessionChannelName } = await import('../realtime/channels');
const {
  acquireTerminal,
  clearMountedTerminal,
  evictTerminalSessions,
  setMountedTerminal,
  useTerminalStore,
} = await import('./terminalStore');

const key = (sessionId: string): string => sessionChannelName('a1', sessionId);

const resident = (): string[] => useTerminalStore.getState().order;

const models = () => useTerminalStore.getState().models;

afterEach(() => {
  for (const model of Object.values(models())) model.dispose();
  useTerminalStore.setState({ models: {}, order: [] });
});

describe('acquireTerminal', () => {
  it('caps the resident set and drops the session idle the longest', () => {
    acquireTerminal('a1', 's1');
    acquireTerminal('a1', 's2');
    acquireTerminal('a1', 's3');
    acquireTerminal('a1', 's4');

    expect(resident()).toEqual([key('s2'), key('s3'), key('s4')]);
    expect(models()[key('s1')]).toBeUndefined();
  });

  it('moves a revisited session to the head of the queue and keeps it resident', () => {
    // The screen calls this on every mount, not only when the model is missing:
    // without the touch the queue degrades from least-recently-used to plain
    // creation order, and the next new session evicts the one the user just
    // came back to — charging them a fresh 4 MiB replay for the revisit.
    const first = acquireTerminal('a1', 's1');
    acquireTerminal('a1', 's2');
    acquireTerminal('a1', 's3');

    expect(acquireTerminal('a1', 's1')).toBe(first);
    expect(resident()).toEqual([key('s2'), key('s3'), key('s1')]);

    acquireTerminal('a1', 's4');

    expect(resident()).toEqual([key('s3'), key('s1'), key('s4')]);
    expect(models()[key('s1')]).toBe(first);
    expect(models()[key('s2')]).toBeUndefined();
  });
});

describe('evictTerminalSessions', () => {
  // The exemption is module state, so a test that leaves it set would hand the
  // next one a terminal nothing can evict.
  afterEach(() => {
    clearMountedTerminal(key('s1'));
    clearMountedTerminal(key('s2'));
  });

  it('spares the terminal on screen while still disposing the rest', () => {
    // A session dying under the user must leave its output readable — the
    // screen says so. Disposing the model behind the visible terminal would
    // wipe the scrollback and hand the screen's re-acquire effect an empty
    // emulator, which is worse than not evicting at all.
    const watched = acquireTerminal('a1', 's1');
    acquireTerminal('a1', 's2');
    setMountedTerminal(key('s1'));

    evictTerminalSessions('a1', ['s1', 's2']);

    expect(models()[key('s1')]).toBe(watched);
    expect(models()[key('s2')]).toBeUndefined();
  });

  it('ignores a clear from the terminal that has already been replaced', () => {
    // Route switches mount the next terminal before the previous one's effect
    // cleanup runs, so the departing screen's clear arrives after its
    // successor claimed the slot. Honouring it would strip the exemption from
    // the terminal the user is now looking at.
    acquireTerminal('a1', 's1');
    const arrived = acquireTerminal('a1', 's2');
    setMountedTerminal(key('s1'));
    setMountedTerminal(key('s2'));

    clearMountedTerminal(key('s1'));
    evictTerminalSessions('a1', ['s1', 's2']);

    expect(models()[key('s1')]).toBeUndefined();
    expect(models()[key('s2')]).toBe(arrived);
  });
});

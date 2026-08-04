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
const { acquireTerminal, useTerminalStore } = await import('./terminalStore');

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

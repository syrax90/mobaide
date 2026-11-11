import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AutoScanCursor } from '../protocol/autoscanFrames';
import { stubLocalStorage } from '../test/localStorageStub';

stubLocalStorage();

type ChannelEvent =
  | { kind: 'connected' }
  | { kind: 'disconnected' }
  | { kind: 'message'; data: string };

/**
 * The `ws/autoscan` channel with the socket taken out: the hook's contract with
 * it is the constructor options, `connect`, `dispose` and the event stream.
 */
class FakeAutoScanChannel {
  static readonly opened: FakeAutoScanChannel[] = [];

  readonly cursor: AutoScanCursor;
  readonly resumeFromSeq: Record<string, bigint>;
  readonly channel: {
    subscribe: (listener: (event: ChannelEvent) => void) => () => void;
    connect: () => void;
    disconnect: () => void;
  };
  connectCount = 0;
  disconnectCount = 0;
  disposed = false;
  private listener: ((event: ChannelEvent) => void) | null = null;

  constructor(
    _agentId: string,
    options: { subscribe?: readonly string[]; resumeFromSeq?: Record<string, bigint> } = {},
  ) {
    this.resumeFromSeq = { ...options.resumeFromSeq };
    this.cursor = new AutoScanCursor(this.resumeFromSeq);
    this.channel = {
      subscribe: (listener) => {
        this.listener = listener;
        return () => {
          this.listener = null;
        };
      },
      connect: () => {
        this.connectCount += 1;
      },
      disconnect: () => {
        this.disconnectCount += 1;
      },
    };
    FakeAutoScanChannel.opened.push(this);
  }

  /**
   * The wrapper's own `connect`, which the production hook calls in preference
   * to `channel.connect()` because the real one re-registers as a disposable
   * first — without that, a revived socket escapes `stopAllRealtime()`.
   */
  connect(): void {
    this.channel.connect();
  }

  emit(event: ChannelEvent): void {
    this.listener?.(event);
  }

  dispose(): void {
    this.disposed = true;
  }
}

vi.mock('../realtime/channels', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../realtime/channels')>();
  return { ...actual, AutoScanChannel: FakeAutoScanChannel };
});

const { useSessionStore } = await import('../auth/sessionStore');
const { autoscanChannelName } = await import('../realtime/channels');
const { useConnectionStore } = await import('../realtime/connectionStore');
const { fakeEventTarget } = await import('../realtime/__testing__/fakeEventTarget');
const { observeTabVisibility } = await import('../realtime/tabVisibility');
const { useAutoScanDetail } = await import('./useAutoScanDetail');

/** Matches `HIDDEN_DISCONNECT_MS` in the hook. */
const HIDDEN_DISCONNECT_MS = 60_000;

const CHANNEL = autoscanChannelName('agent-1');

let frames: (() => void)[] = [];

function flushFrames(): void {
  const due = frames;
  frames = [];
  for (const callback of due) callback();
}

/** One live `log` event, as it arrives off the wire. */
function logFrame(seq: number, line: string): ChannelEvent {
  return {
    kind: 'message',
    data: JSON.stringify({
      type: 'event',
      project_name: 'alpha',
      event: { seq, ts: '2026-08-05T10:00:00Z', type: 'log', payload: { line } },
    }),
  };
}

function opened(index = 0): FakeAutoScanChannel | undefined {
  return FakeAutoScanChannel.opened[index];
}

function mount() {
  return renderHook(() => useAutoScanDetail('agent-1', 'alpha'));
}

beforeEach(() => {
  FakeAutoScanChannel.opened.length = 0;
  frames = [];
  vi.stubGlobal('requestAnimationFrame', (callback: () => void) => frames.push(callback));
  vi.stubGlobal('cancelAnimationFrame', (handle: number) => {
    frames[handle - 1] = () => undefined;
  });
  useConnectionStore.setState({ channels: {} });
  useSessionStore.setState({ authState: { kind: 'fullyConnected' } });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useSessionStore.setState({ authState: { kind: 'unauthenticated' } });
});

describe('useAutoScanDetail', () => {
  it('owns one channel and disposes it on unmount', () => {
    const view = mount();
    expect(FakeAutoScanChannel.opened).toHaveLength(1);
    expect(opened()?.connectCount).toBeGreaterThan(0);

    view.unmount();

    expect(opened()?.disposed).toBe(true);
  });

  it('holds a burst of frames until the next animation frame, then applies it in order', () => {
    const view = mount();

    act(() => {
      opened()?.emit(logFrame(1, 'one'));
    });
    act(() => {
      opened()?.emit(logFrame(2, 'two'));
    });
    expect(view.result.current.state.logLines).toHaveLength(0);

    act(() => {
      flushFrames();
    });

    expect(view.result.current.state.logLines.map((line) => line.text)).toEqual(['one', 'two']);
  });

  it('flushes what it buffered when the channel is torn down mid-burst', () => {
    const view = mount();
    act(() => {
      opened()?.emit(logFrame(7, 'last gasp'));
    });

    act(() => {
      useSessionStore.setState({ authState: { kind: 'unauthenticated' } });
    });

    expect(view.result.current.state.logLines.map((line) => line.text)).toEqual(['last gasp']);
  });

  it('resumes a re-created channel from the live cursor, not the one captured at mount', () => {
    mount();
    expect(opened()?.resumeFromSeq).toEqual({});
    act(() => {
      opened()?.emit(logFrame(42, 'built'));
      flushFrames();
    });

    act(() => {
      useSessionStore.setState({ authState: { kind: 'unauthenticated' } });
    });
    act(() => {
      useSessionStore.setState({ authState: { kind: 'fullyConnected' } });
    });

    expect(FakeAutoScanChannel.opened).toHaveLength(2);
    expect(opened(1)?.resumeFromSeq).toEqual({ alpha: 42n });
  });

  it('revives the same channel after the realtime layer forgets it', () => {
    mount();
    act(() => {
      useConnectionStore
        .getState()
        .setChannelState(CHANNEL, { status: 'connected', attempt: 0, lastCloseCode: null });
    });
    const before = opened()?.connectCount ?? 0;

    act(() => {
      useConnectionStore.getState().forgetChannel(CHANNEL);
    });

    // Reconnected in place: re-creating (or disposing) here is the oscillation
    // the two-effect split exists to prevent.
    expect(FakeAutoScanChannel.opened).toHaveLength(1);
    expect(opened()?.connectCount).toBe(before + 1);
    expect(opened()?.disposed).toBe(false);
  });

  it('latches an agent decline and stops reconnecting instead of looping', () => {
    // `1013` arrives *after* the socket was accepted, so the backoff ladder has
    // already been reset — leaving the default reconnect policy in charge would
    // re-handshake at the ~1 s floor for as long as the screen stayed open.
    const view = mount();

    act(() => {
      useConnectionStore
        .getState()
        .setChannelState(CHANNEL, { status: 'connecting', attempt: 1, lastCloseCode: 1013 });
    });

    expect(view.result.current.declined).toBe(true);
    expect(opened()?.disconnectCount).toBe(1);

    // And the revival effect must not undo it: `disconnect()` deliberately does
    // not `forgetChannel`, but the latch is what guarantees it either way.
    const reconnects = opened()?.connectCount ?? 0;
    act(() => {
      useConnectionStore.getState().forgetChannel(CHANNEL);
    });
    expect(opened()?.connectCount).toBe(reconnects);
  });

  it('clears the latch when the channel is rebuilt', () => {
    // The ownership effect re-runs on an auth flip. A latch carried across that
    // rebuild would claim the agent refused a socket that is delivering frames.
    const view = mount();
    act(() => {
      useConnectionStore
        .getState()
        .setChannelState(CHANNEL, { status: 'connecting', attempt: 1, lastCloseCode: 1013 });
    });
    expect(view.result.current.declined).toBe(true);

    act(() => {
      useSessionStore.setState({ authState: { kind: 'unauthenticated' } });
    });
    act(() => {
      useSessionStore.setState({ authState: { kind: 'fullyConnected' } });
    });

    expect(view.result.current.declined).toBe(false);
  });

  it('retries once when the user asks, clearing the latch', () => {
    const view = mount();
    act(() => {
      useConnectionStore
        .getState()
        .setChannelState(CHANNEL, { status: 'connecting', attempt: 1, lastCloseCode: 1013 });
    });
    const before = opened()?.connectCount ?? 0;

    act(() => {
      view.result.current.retry();
    });

    expect(view.result.current.declined).toBe(false);
    expect(opened()?.connectCount).toBe(before + 1);
  });
});

describe('useAutoScanDetail tab visibility', () => {
  /** Mounts the hook with a fake document driving `tabVisibility`. */
  function mountWithDoc() {
    const doc = fakeEventTarget();
    const uninstall = observeTabVisibility(doc);
    const view = mount();
    const set = (hidden: boolean): void => {
      act(() => {
        doc.hidden = hidden;
        doc.fire('visibilitychange');
      });
    };
    return { view, set, uninstall };
  }

  it('holds the socket through a brief hide', () => {
    // `resumeOnFocus` documents the rule: alt-tabbing is constant, and a
    // handshake per `visibilitychange` would be its own storm.
    vi.useFakeTimers();
    try {
      const { set, uninstall } = mountWithDoc();
      const before = opened()?.disconnectCount ?? 0;

      set(true);
      act(() => {
        vi.advanceTimersByTime(HIDDEN_DISCONNECT_MS - 1);
      });
      expect(opened()?.disconnectCount).toBe(before);

      set(false);
      act(() => {
        vi.advanceTimersByTime(HIDDEN_DISCONNECT_MS * 2);
      });
      expect(opened()?.disconnectCount).toBe(before);
      uninstall();
    } finally {
      vi.useRealTimers();
    }
  });

  it('closes the socket once the tab stays hidden past the grace', () => {
    vi.useFakeTimers();
    try {
      const { set, uninstall } = mountWithDoc();
      const before = opened()?.disconnectCount ?? 0;

      set(true);
      act(() => {
        vi.advanceTimersByTime(HIDDEN_DISCONNECT_MS);
      });

      expect(opened()?.disconnectCount).toBe(before + 1);
      uninstall();
    } finally {
      vi.useRealTimers();
    }
  });

  it('reopens the socket when the tab comes back', () => {
    // Lossless: `onOpen` rebuilds the subscribe frame from the live wire
    // cursor, so the resume picks up at the high-water mark.
    vi.useFakeTimers();
    try {
      const { set, uninstall } = mountWithDoc();
      set(true);
      act(() => {
        vi.advanceTimersByTime(HIDDEN_DISCONNECT_MS);
      });
      const before = opened()?.connectCount ?? 0;

      set(false);

      expect(opened()?.connectCount).toBe(before + 1);
      uninstall();
    } finally {
      vi.useRealTimers();
    }
  });

  it('arms the disconnect when it mounts into an already-hidden tab', () => {
    // `onTabVisibility` reports transitions, not the current value, so a hook
    // that mounts while hidden gets no edge to react to and would stream until
    // the tab was both shown and hidden again.
    vi.useFakeTimers();
    try {
      const doc = fakeEventTarget();
      doc.hidden = true;
      const uninstall = observeTabVisibility(doc);
      mount();
      const before = opened()?.disconnectCount ?? 0;

      act(() => {
        vi.advanceTimersByTime(HIDDEN_DISCONNECT_MS);
      });

      expect(opened()?.disconnectCount).toBe(before + 1);
      uninstall();
    } finally {
      vi.useRealTimers();
    }
  });

  it('leaves a declined channel declined across a hide and show', () => {
    // The agent turned this subscription down; only the banner's retry may
    // re-arm it, and a tab switch is not that.
    vi.useFakeTimers();
    try {
      const { view, set, uninstall } = mountWithDoc();
      act(() => {
        useConnectionStore
          .getState()
          .setChannelState(CHANNEL, { status: 'stopped', attempt: 0, lastCloseCode: 1013 });
      });
      expect(view.result.current.declined).toBe(true);
      const before = opened()?.connectCount ?? 0;

      set(true);
      act(() => {
        vi.advanceTimersByTime(HIDDEN_DISCONNECT_MS);
      });
      set(false);

      expect(opened()?.connectCount).toBe(before);
      uninstall();
    } finally {
      vi.useRealTimers();
    }
  });
});

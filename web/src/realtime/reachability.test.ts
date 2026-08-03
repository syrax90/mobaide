import { onlineManager } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  observeReachability,
  reportRealtimeStopped,
  reportRestReachable,
  resetReachability,
} from './reachability';
import type { ChannelEvent } from './webSocketChannel';

/** The `/api/version` probe, with the server's answer under test control. */
function stubProbe() {
  const state = { calls: 0, answers: false };
  const probeFetch: typeof fetch = () => {
    state.calls += 1;
    return state.answers
      ? // Any status proves the server is there — 503 is deliberate.
        Promise.resolve(new Response(null, { status: 503 }))
      : Promise.reject(new Error('unreachable'));
  };
  return { state, probeFetch };
}

/** Feeds channel events into the observer the way a live channel would. */
function fakeChannel() {
  let listener: ((event: ChannelEvent) => void) | null = null;
  const unsubscribe = observeReachability((fn) => {
    listener = fn;
    return () => {
      listener = null;
    };
  });
  return {
    unsubscribe,
    emit(...events: ChannelEvent[]) {
      for (const event of events) listener?.(event);
    },
  };
}

let probe: ReturnType<typeof stubProbe>;

beforeEach(() => {
  vi.useFakeTimers();
  probe = stubProbe();
  resetReachability(probe.probeFetch);
});

afterEach(() => {
  resetReachability();
  vi.useRealTimers();
});

describe('observeReachability', () => {
  it('rides out a single failed connect attempt', () => {
    // Going offline pauses mutations too, so one blip must not freeze the UI.
    fakeChannel().emit({ kind: 'disconnected' });

    expect(onlineManager.isOnline()).toBe(true);
  });

  it('declares offline once a probe confirms the server is gone', async () => {
    // Two pre-open failures raise the question; the probe answers it. A failing
    // socket on its own is never enough, because that is also what a blocked
    // WebSocket upgrade over a healthy network looks like.
    fakeChannel().emit({ kind: 'disconnected' }, { kind: 'disconnected' });
    await vi.advanceTimersByTimeAsync(0);

    expect(probe.state.calls).toBe(1);
    expect(onlineManager.isOnline()).toBe(false);
  });

  it('stays online when the confirming probe finds the server answering', async () => {
    // The blocked-upgrade case: HTTP works, so the app must not go offline at
    // all — not go offline and get rescued by the next probe.
    probe.state.answers = true;
    fakeChannel().emit({ kind: 'disconnected' }, { kind: 'disconnected' });
    await vi.advanceTimersByTimeAsync(0);

    expect(onlineManager.isOnline()).toBe(true);
  });

  it('does not count a dropped established connection as a failure', () => {
    // Losing a socket that was up is one event; never getting one up is the
    // pattern that means the server is not there.
    fakeChannel().emit(
      { kind: 'connected' },
      { kind: 'disconnected' },
      { kind: 'connected' },
      { kind: 'disconnected' },
    );

    expect(onlineManager.isOnline()).toBe(true);
  });

  it('restarts the failure count once the server answers again', () => {
    const channel = fakeChannel();
    channel.emit({ kind: 'disconnected' }, { kind: 'disconnected' });

    reportRestReachable();
    channel.emit({ kind: 'disconnected' });

    expect(onlineManager.isOnline()).toBe(true);
  });
});

describe('reachability recovery', () => {
  it('goes back online when a REST call answers, and stops probing', async () => {
    fakeChannel().emit({ kind: 'disconnected' }, { kind: 'disconnected' });
    await vi.advanceTimersByTimeAsync(0);
    const probesBeforeRecovery = probe.state.calls;

    reportRestReachable();
    await vi.advanceTimersByTimeAsync(60_000);

    expect(onlineManager.isOnline()).toBe(true);
    // The interval is torn down, so a whole minute adds no further probes.
    expect(probe.state.calls).toBe(probesBeforeRecovery);
  });

  it('goes back online when the channel is stopped on purpose', () => {
    // The signed-out state: the login and reset mutations must never be paused.
    fakeChannel().emit({ kind: 'disconnected' }, { kind: 'disconnected' });

    reportRealtimeStopped();

    expect(onlineManager.isOnline()).toBe(true);
  });

  it('recovers through the periodic probe when the socket cannot', async () => {
    // A proxy that blocks the WS upgrade while passing HTTP would otherwise pin
    // the app offline forever, because TanStack pauses the requests that would
    // clear the flag.
    fakeChannel().emit({ kind: 'disconnected' }, { kind: 'disconnected' });
    await vi.advanceTimersByTimeAsync(0);

    // The confirming probe failed, so the interval takes over from here.
    expect(probe.state.calls).toBe(1);
    expect(onlineManager.isOnline()).toBe(false);

    await vi.advanceTimersByTimeAsync(15_000);
    expect(probe.state.calls).toBe(2);
    expect(onlineManager.isOnline()).toBe(false);

    probe.state.answers = true;
    await vi.advanceTimersByTimeAsync(15_000);

    expect(probe.state.calls).toBe(3);
    expect(onlineManager.isOnline()).toBe(true);
  });

  it('disbelieves a WebSocket-only failure without even probing', () => {
    // Fresh REST evidence already answers the question, so no probe is spent.
    reportRestReachable();
    fakeChannel().emit({ kind: 'disconnected' }, { kind: 'disconnected' });

    expect(onlineManager.isOnline()).toBe(true);
    expect(probe.state.calls).toBe(0);
  });

  it('keeps honouring the two-failure rule after suppressing a transition', () => {
    // The suppressing branch has to reset the counter, or the first failure
    // after the trust window expired would flip offline on its own.
    reportRestReachable();
    const channel = fakeChannel();
    channel.emit({ kind: 'disconnected' }, { kind: 'disconnected' });
    vi.advanceTimersByTime(61_000);

    channel.emit({ kind: 'disconnected' });

    expect(probe.state.calls).toBe(0);
    expect(onlineManager.isOnline()).toBe(true);
  });

  it('probes again once the REST evidence goes stale', async () => {
    reportRestReachable();
    vi.advanceTimersByTime(61_000);

    fakeChannel().emit({ kind: 'disconnected' }, { kind: 'disconnected' });
    await vi.advanceTimersByTimeAsync(0);

    expect(onlineManager.isOnline()).toBe(false);
  });
});

describe('reachability probe races', () => {
  /** A probe whose settlement the test controls, so overlap is deterministic. */
  function deferredProbe() {
    const settlers: { resolve: () => void; reject: () => void }[] = [];
    const state = { calls: 0 };
    const probeFetch: typeof fetch = () =>
      new Promise<Response>((resolve, reject) => {
        state.calls += 1;
        settlers.push({
          resolve: () => {
            resolve(new Response(null, { status: 200 }));
          },
          reject: () => {
            reject(new Error('unreachable'));
          },
        });
      });
    return { state, probeFetch, settlers };
  }

  it('does not go offline off a probe that is merely already in flight', async () => {
    // Two callers overlap inside the 5 s probe timeout — the second used to be
    // told "false" meaning "busy" and read it as "the server is gone".
    const deferred = deferredProbe();
    resetReachability(deferred.probeFetch);
    const channel = fakeChannel();

    channel.emit({ kind: 'disconnected' }, { kind: 'disconnected' });
    await vi.advanceTimersByTimeAsync(0);
    channel.emit({ kind: 'disconnected' }, { kind: 'disconnected' });
    await vi.advanceTimersByTimeAsync(0);

    // One shared request, and nothing decided while it is pending.
    expect(deferred.state.calls).toBe(1);
    expect(onlineManager.isOnline()).toBe(true);

    deferred.settlers[0]?.resolve();
    await vi.advanceTimersByTimeAsync(0);
    expect(onlineManager.isOnline()).toBe(true);
  });

  it('lets the socket coming back outrank a probe that rejects afterwards', async () => {
    const deferred = deferredProbe();
    resetReachability(deferred.probeFetch);
    const channel = fakeChannel();

    channel.emit({ kind: 'disconnected' }, { kind: 'disconnected' });
    await vi.advanceTimersByTimeAsync(0);

    // Newer positive evidence lands while the confirmation is still pending.
    channel.emit({ kind: 'connected' });
    deferred.settlers[0]?.reject();
    await vi.advanceTimersByTimeAsync(0);

    expect(onlineManager.isOnline()).toBe(true);
  });
});

describe('ownership of onlineManager', () => {
  it('does not let a browser offline event strand the app offline', () => {
    // TanStack's default listener sets the flag directly. With it still
    // installed, `goOnline()` took its already-online early return and never
    // restored the flag — including on sign-out, which must never leave the
    // login mutations paused.
    onlineManager.setOnline(false);

    reportRealtimeStopped();

    expect(onlineManager.isOnline()).toBe(true);
  });
});

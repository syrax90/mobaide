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

  it('declares offline on the second consecutive pre-open failure', () => {
    fakeChannel().emit({ kind: 'disconnected' }, { kind: 'disconnected' });

    expect(onlineManager.isOnline()).toBe(false);
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

    // Probed at once rather than only on the first tick: TanStack pauses
    // mutations while offline, so an arming delay is dead UI time.
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

  it('disbelieves a WebSocket-only failure while HTTP is proven reachable', () => {
    // The blocked-upgrade case: without this the app would flip offline every
    // time the socket failed twice, and back online on the next probe, forever
    // — each flip firing a full refetch sweep.
    reportRestReachable();
    fakeChannel().emit({ kind: 'disconnected' }, { kind: 'disconnected' });

    expect(onlineManager.isOnline()).toBe(true);
    expect(probe.state.calls).toBe(0);
  });

  it('stops disbelieving once the REST evidence goes stale', () => {
    reportRestReachable();
    vi.advanceTimersByTime(61_000);

    fakeChannel().emit({ kind: 'disconnected' }, { kind: 'disconnected' });

    expect(onlineManager.isOnline()).toBe(false);
  });
});

import { describe, expect, it } from 'vitest';
import { WS_CLOSE } from '../protocol/closeCodes';
import {
  FakeSocket,
  fakeScheduler,
  recordingSocketFactory,
  settle,
} from './__testing__/fakeSocket';
import {
  WebSocketChannel,
  type ChannelEvent,
  type ChannelState,
  type WebSocketChannelOptions,
} from './webSocketChannel';

const CHANNEL_URL = 'ws://relay.test/ws/user-events?token=t1';

function harness(overrides: Partial<WebSocketChannelOptions> = {}) {
  const { sockets, socketFactory } = recordingSocketFactory();
  const events: ChannelEvent[] = [];
  const states: ChannelState[] = [];
  const timers = fakeScheduler();
  const channel = new WebSocketChannel({
    name: 'user-events',
    axis: 'user',
    buildUrl: () => Promise.resolve(CHANNEL_URL),
    socketFactory,
    scheduler: timers.scheduler,
    // Jitter at its ceiling, so every delay below is the bare ladder value.
    random: () => 1,
    onStateChange: (_name, state) => states.push(state),
    ...overrides,
  });
  channel.subscribe((event) => events.push(event));
  return {
    channel,
    sockets,
    events,
    timers,
    kinds: () => events.map((event) => event.kind),
  };
}

async function connectAndAccept(h: ReturnType<typeof harness>): Promise<FakeSocket> {
  h.channel.connect();
  await settle();
  const socket = h.sockets.at(-1);
  if (socket === undefined) throw new Error('no socket was created');
  socket.accept();
  return socket;
}

describe('WebSocketChannel open', () => {
  it('runs the open hook before announcing connected', async () => {
    // The auto-scan subscribe window is 200 ms direct / 1 s through the relay,
    // so the resume frame has to be on the wire before any listener reacts.
    const order: string[] = [];
    const h = harness({
      onOpen: (send) => {
        order.push('open-hook');
        send('resume');
      },
    });
    h.channel.subscribe((event) => {
      if (event.kind === 'connected') order.push('connected');
    });

    const socket = await connectAndAccept(h);

    expect(socket.url).toBe(CHANNEL_URL);
    expect(order).toEqual(['open-hook', 'connected']);
    expect(socket.sent).toEqual(['resume']);
  });

  it('stops without a reconnect when there is no connectable URL', async () => {
    // Signed out, or no agent token could be minted: burning the ladder on a
    // URL that cannot exist would just spin.
    const h = harness({ buildUrl: () => Promise.resolve(null) });

    h.channel.connect();
    await settle();

    expect(h.sockets).toHaveLength(0);
    expect(h.kinds()).toEqual(['disconnected']);
    expect(h.timers.pending).toHaveLength(0);
    expect(h.channel.currentState.status).toBe('stopped');
  });

  it('surfaces a failed URL build as an error and stops', async () => {
    const h = harness({ buildUrl: () => Promise.reject(new Error('mint failed')) });

    h.channel.connect();
    await settle();

    expect(h.events).toEqual([{ kind: 'error', message: 'mint failed' }]);
    expect(h.sockets).toHaveLength(0);
    expect(h.timers.pending).toHaveLength(0);
  });

  it('splits text and binary frames and ignores anything else', async () => {
    const h = harness({ binary: true });
    const socket = await connectAndAccept(h);
    const payload = new Uint8Array([0x6f, 0x41]).buffer;

    socket.deliver('{"type":"ping"}');
    socket.deliver(payload);
    // A Blob is what arrives if `binaryType` were ever left at its default;
    // dropping it is better than emitting a frame the codec cannot read.
    socket.deliver(new Blob(['ignored']));

    expect(socket.binaryType).toBe('arraybuffer');
    expect(h.events).toEqual([
      { kind: 'connected' },
      { kind: 'message', data: '{"type":"ping"}' },
      { kind: 'binary', data: payload },
    ]);
  });
});

describe('WebSocketChannel reconnect', () => {
  it('restarts the backoff ladder after a successful open', async () => {
    const h = harness();
    h.channel.connect();
    await settle();

    h.sockets[0]?.serverClose(1006);
    const first = h.timers.runNext();
    await settle();
    h.sockets[1]?.serverClose(1006);
    const second = h.timers.runNext();
    await settle();

    h.sockets[2]?.accept();
    h.sockets[2]?.serverClose(1006);

    expect([first, second, h.timers.runNext()]).toEqual([1000, 2000, 1000]);
  });

  it('settles a socket once when the browser reports both error and close', async () => {
    const h = harness();
    h.channel.connect();
    await settle();
    const socket = h.sockets[0];

    socket?.fail();
    socket?.serverClose(1006);

    expect(h.kinds()).toEqual(['error', 'disconnected']);
    expect(h.timers.pending).toHaveLength(1);
  });

  it('drops a superseded connect attempt instead of opening a rival socket', async () => {
    const urls: ((url: string | null) => void)[] = [];
    const h = harness({
      buildUrl: () => new Promise<string | null>((resolve) => urls.push(resolve)),
    });

    h.channel.connect();
    h.channel.forceReconnect();
    urls[0]?.(CHANNEL_URL);
    urls[1]?.(CHANNEL_URL);
    await settle();

    expect(urls).toHaveLength(2);
    expect(h.sockets).toHaveLength(1);
    h.sockets[0]?.accept();
    expect(h.kinds()).toEqual(['connected']);
  });

  it('ignores a connect while one is already in flight or live', async () => {
    const h = harness();

    h.channel.connect();
    h.channel.connect();
    await settle();
    h.channel.connect();

    expect(h.sockets).toHaveLength(1);
  });

  it('ignores a connect while a backoff reconnect is armed', async () => {
    const h = harness();
    h.channel.connect();
    await settle();
    h.sockets[0]?.serverClose(1006);

    h.channel.connect();
    await settle();

    expect(h.sockets).toHaveLength(1);
    expect(h.timers.pending).toHaveLength(1);
  });
});

describe('WebSocketChannel close handling', () => {
  it('applies the close policy: a rejected user credential is terminal', async () => {
    const h = harness();
    const socket = await connectAndAccept(h);

    socket.serverClose(WS_CLOSE.authFailed);

    expect(h.kinds()).toEqual(['connected', 'disconnected', 'tokenRejected']);
    expect(h.timers.pending).toHaveLength(0);
    expect(h.channel.currentState).toEqual({
      status: 'stopped',
      attempt: 0,
      lastCloseCode: WS_CLOSE.authFailed,
    });
  });

  it('drops the cached agent token and keeps reconnecting on 4003', async () => {
    let cleared = 0;
    const h = harness({
      axis: 'agent',
      onClearAgentToken: () => {
        cleared += 1;
      },
    });
    const socket = await connectAndAccept(h);

    socket.serverClose(WS_CLOSE.tokenNotValidForAgent);

    expect(cleared).toBe(1);
    expect(h.kinds()).toEqual(['connected', 'disconnected']);
    expect(h.timers.pending).toHaveLength(1);
  });

  it('closes with the only code a browser may send and deafens the socket', async () => {
    const h = harness();
    const socket = await connectAndAccept(h);

    h.channel.disconnect();
    // A close frame that lands after the client gave up must not resurrect the
    // backoff loop.
    socket.serverClose(WS_CLOSE.agentTransientOffline);

    expect(socket.clientCloses).toEqual([WS_CLOSE.normal]);
    expect(h.kinds()).toEqual(['connected']);
    expect(h.timers.pending).toHaveLength(0);
    expect(h.channel.currentState).toEqual({
      status: 'stopped',
      attempt: 0,
      lastCloseCode: null,
    });
  });
});

describe('WebSocketChannel send', () => {
  it('reports a send attempted with no open socket', () => {
    const h = harness();

    expect(h.channel.send('ping')).toBe(false);
    expect(h.events).toEqual([{ kind: 'error', message: 'WebSocket send failed' }]);
  });

  it('forwards a payload once the socket is open', async () => {
    const h = harness();
    const socket = await connectAndAccept(h);

    expect(h.channel.send('ping')).toBe(true);
    expect(socket.sent).toEqual(['ping']);
  });
});

describe('WebSocketChannel socket acquisition', () => {
  it('recovers from a socket constructor that throws synchronously', async () => {
    // `new WebSocket(url)` throws SyntaxError on a URL the browser rejects and
    // SecurityError when CSP refuses the scheme. Unguarded this became an
    // unhandled rejection and the channel never reconnected.
    let attempts = 0;
    const { sockets, socketFactory } = recordingSocketFactory();
    const timers = fakeScheduler();
    const events: ChannelEvent[] = [];
    const channel = new WebSocketChannel({
      name: 'user-events',
      axis: 'user',
      buildUrl: () => Promise.resolve(CHANNEL_URL),
      scheduler: timers.scheduler,
      random: () => 1,
      socketFactory: (url) => {
        attempts += 1;
        if (attempts === 1) throw new DOMException('refused by CSP', 'SecurityError');
        return socketFactory(url);
      },
    });
    channel.subscribe((event) => events.push(event));

    channel.connect();
    await settle();

    expect(events.map((event) => event.kind)).toEqual(['error', 'disconnected']);
    expect(timers.pending).toHaveLength(1);

    timers.runNext();
    await settle();
    expect(sockets).toHaveLength(1);
  });

  it('opens one socket when a superseded attempt resolves mid-flight', async () => {
    // The in-flight flag is shared across generations; a stale attempt clearing
    // it used to reopen the connect() guard and allow a third rival socket.
    const { sockets, socketFactory } = recordingSocketFactory();
    const timers = fakeScheduler();
    const resolvers: ((url: string) => void)[] = [];
    const channel = new WebSocketChannel({
      name: 'user-events',
      axis: 'user',
      buildUrl: () => new Promise<string>((resolve) => resolvers.push(resolve)),
      scheduler: timers.scheduler,
      random: () => 1,
      socketFactory,
    });

    channel.connect();
    channel.forceReconnect();
    // The superseded attempt lands first.
    resolvers[0]?.(CHANNEL_URL);
    await settle();
    channel.connect();
    await settle();
    resolvers[1]?.(CHANNEL_URL);
    await settle();

    expect(sockets).toHaveLength(1);
  });
});

describe('WebSocketChannel teardown during a pending token mint', () => {
  it('writes no state back after disconnect when the URL build rejects', async () => {
    // The registry drops the connection-store entry on teardown; a late
    // setState here would re-insert it with nobody left to remove it.
    const states: ChannelState[] = [];
    const events: ChannelEvent[] = [];
    let rejectUrl: ((reason: Error) => void) | undefined;
    const channel = new WebSocketChannel({
      name: 'user-events',
      axis: 'user',
      buildUrl: () =>
        new Promise<string>((_resolve, reject) => {
          rejectUrl = reject;
        }),
      scheduler: fakeScheduler().scheduler,
      random: () => 1,
      socketFactory: recordingSocketFactory().socketFactory,
      onStateChange: (_name, state) => states.push(state),
    });
    channel.subscribe((event) => events.push(event));

    channel.connect();
    channel.disconnect();
    const statesAtTeardown = states.length;

    rejectUrl?.(new Error('mint failed'));
    await settle();

    expect(states).toHaveLength(statesAtTeardown);
    expect(events).toEqual([]);
  });
});

describe('WebSocketChannel error reporting', () => {
  it('never puts the token into an event a consumer might render', async () => {
    // Chromium's SyntaxError for a rejected URL quotes the whole URL back, and
    // the house convention for an error string is to toast it into the DOM.
    const events: ChannelEvent[] = [];
    const channel = new WebSocketChannel({
      name: 'user-events',
      axis: 'user',
      buildUrl: () => Promise.resolve(CHANNEL_URL),
      scheduler: fakeScheduler().scheduler,
      random: () => 1,
      socketFactory: () => {
        throw new Error(
          "Failed to construct 'WebSocket': The URL 'wss://h/x?token=SECRETJWT' is invalid.",
        );
      },
    });
    channel.subscribe((event) => events.push(event));

    channel.connect();
    await settle();

    const message = events.find((event) => event.kind === 'error')?.message ?? '';
    expect(message).not.toContain('SECRETJWT');
    expect(message).toContain('token=<redacted>');
  });
});

describe('WebSocketChannel agent credential rotation', () => {
  function agentHarness(buildUrl: () => Promise<string | null>) {
    const { sockets, socketFactory } = recordingSocketFactory();
    const timers = fakeScheduler();
    let cleared = 0;
    const channel = new WebSocketChannel({
      name: 'agent-events:A1',
      axis: 'agent',
      buildUrl,
      scheduler: timers.scheduler,
      random: () => 1,
      socketFactory,
      onClearAgentToken: () => {
        cleared += 1;
      },
    });
    return { channel, sockets, timers, cleared: () => cleared };
  }

  it('drops the cached token after three failures to even open', async () => {
    // A per-agent JWT minted while its TTL was 0 has no exp, so nothing else
    // ever rotates it, and the relay's 4001 arrives as an opaque 1006.
    const h = agentHarness(() => Promise.resolve('ws://relay.test/relay/ws/events/A1?token=t'));

    h.channel.connect();
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      await settle();
      h.sockets.at(-1)?.serverClose(1006);
      if (attempt < 3) h.timers.runNext();
    }

    expect(h.cleared()).toBe(1);
  });

  it('does not rotate on a socket that opened before dropping', async () => {
    const h = agentHarness(() => Promise.resolve('ws://relay.test/relay/ws/events/A1?token=t'));

    h.channel.connect();
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      await settle();
      h.sockets.at(-1)?.accept();
      h.sockets.at(-1)?.serverClose(1006);
      if (attempt < 3) h.timers.runNext();
    }

    expect(h.cleared()).toBe(0);
  });

  it('keeps backing off when a mint fails, rather than stranding the channel', async () => {
    // On the agent axis a null URL usually means the network mint just failed,
    // unlike the user axis where it means signed out.
    const h = agentHarness(() => Promise.resolve(null));

    h.channel.connect();
    await settle();

    expect(h.timers.pending).toHaveLength(1);
    expect(h.channel.currentState.status).toBe('connecting');
  });
});

describe('WebSocketChannel unbuildable agent URLs', () => {
  it('stops retrying a mint that keeps failing, instead of looping forever', async () => {
    // `ensureAgentToken` collapses every error to null, including the permanent
    // 403 that follows a 4003 "user does not own this agent" close.
    const timers = fakeScheduler();
    const { sockets, socketFactory } = recordingSocketFactory();
    const channel = new WebSocketChannel({
      name: 'agent-events:A1',
      axis: 'agent',
      buildUrl: () => Promise.resolve(null),
      scheduler: timers.scheduler,
      random: () => 1,
      socketFactory,
    });

    channel.connect();
    for (let attempt = 0; attempt < 10; attempt += 1) {
      await settle();
      if (timers.pending.length === 0) break;
      timers.runNext();
    }

    expect(sockets).toHaveLength(0);
    expect(timers.pending).toHaveLength(0);
    expect(channel.currentState.status).toBe('stopped');
  });

  it('forgets earlier mint failures once one succeeds', async () => {
    const timers = fakeScheduler();
    const { sockets, socketFactory } = recordingSocketFactory();
    let mintFails = 3;
    const channel = new WebSocketChannel({
      name: 'agent-events:A1',
      axis: 'agent',
      buildUrl: () => {
        if (mintFails > 0) {
          mintFails -= 1;
          return Promise.resolve(null);
        }
        return Promise.resolve('ws://relay.test/relay/ws/events/A1?token=t');
      },
      scheduler: timers.scheduler,
      random: () => 1,
      socketFactory,
    });

    channel.connect();
    for (let attempt = 0; attempt < 4; attempt += 1) {
      await settle();
      if (timers.pending.length > 0) timers.runNext();
    }
    await settle();

    expect(sockets).toHaveLength(1);
  });
});

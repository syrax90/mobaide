import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  fakeScheduler,
  recordingSocketFactory,
  settle,
} from './__testing__/fakeSocket';
import { useConnectionStore } from './connectionStore';
import { disposeAllChannels } from './liveChannels';

// The credential seams are the only part of WEB-03 these channels touch, and
// both would go to the network. Everything else in the graph is the real thing.
const auth = vi.hoisted<{ userToken: string | null; agentToken: string | null }>(() => ({
  userToken: 'user-jwt',
  agentToken: 'agent-jwt',
}));

vi.mock('../auth/sessionTransport', () => ({
  getAccessToken: () => auth.userToken,
}));
vi.mock('../auth/userTokens', () => ({
  ensureFreshUserToken: () => Promise.resolve(),
}));
vi.mock('../auth/agentTokens', () => ({
  ensureAgentToken: () => Promise.resolve(auth.agentToken),
  clearAgentToken: () => undefined,
}));

const { AutoScanChannel, SessionIoChannel, createAgentEventsChannel, createUserEventsChannel } =
  await import('./channels');

function testSeams() {
  const { sockets, socketFactory } = recordingSocketFactory();
  const timers = fakeScheduler();
  return {
    sockets,
    overrides: {
      scheduler: timers.scheduler,
      // Jitter at its ceiling, so a delay assertion is the bare ladder value.
      random: () => 1,
      socketFactory,
    },
    runReconnect(): void {
      timers.runNext();
    },
  };
}

function wsOrigin(): string {
  return window.location.origin.replace(/^http/, 'ws');
}

function textFrame(value: string | ArrayBufferView | undefined): unknown {
  if (typeof value !== 'string') throw new Error('expected a text frame');
  const parsed: unknown = JSON.parse(value);
  return parsed;
}

function byteFrame(value: string | ArrayBufferView | undefined): number[] {
  if (!(value instanceof Uint8Array)) throw new Error('expected a binary frame');
  return [...value];
}

beforeEach(() => {
  auth.userToken = 'user-jwt';
  auth.agentToken = 'agent-jwt';
});

describe('channel URLs', () => {
  it('builds the user channel against the origin with no relay rewrite', async () => {
    // `ws/user-events` is an account endpoint: routed through the relay rewrite
    // it would land at `relay/ws/user-events/{agentId}` and 404.
    const seams = testSeams();

    createUserEventsChannel(seams.overrides).connect();
    await settle();

    expect(seams.sockets[0]?.url).toBe(`${wsOrigin()}/ws/user-events?token=user-jwt`);
  });

  it('rewrites the agent channel onto the relay tunnel', async () => {
    const seams = testSeams();

    createAgentEventsChannel('a1', seams.overrides).connect();
    await settle();

    expect(seams.sockets[0]?.url).toBe(`${wsOrigin()}/relay/ws/events/a1?token=agent-jwt`);
  });

  it('opens no socket when no agent token can be minted', async () => {
    auth.agentToken = null;
    const seams = testSeams();
    const channel = createAgentEventsChannel('a1', seams.overrides);
    const events: string[] = [];
    channel.subscribe((event) => events.push(event.kind));

    channel.connect();
    await settle();

    expect(seams.sockets).toHaveLength(0);
    expect(events).toEqual(['disconnected']);
  });
});

describe('SessionIoChannel', () => {
  it('re-asserts the measured grid on every open', async () => {
    // The agent spawns every session at 24x80 and never reports a size back, so
    // a reconnect that skipped this would leave a full-screen TUI in 24 rows.
    const seams = testSeams();
    const io = new SessionIoChannel('a1', 's1', seams.overrides);
    io.setSize(40, 120);

    io.channel.connect();
    await settle();
    seams.sockets[0]?.accept();
    seams.sockets[0]?.serverClose(1006);
    seams.runReconnect();
    await settle();
    seams.sockets[1]?.accept();

    expect(byteFrame(seams.sockets[0]?.sent[0])).toEqual([0x72, 0, 40, 0, 120]);
    expect(byteFrame(seams.sockets[1]?.sent[0])).toEqual([0x72, 0, 40, 0, 120]);
  });

  it('clamps a degenerate measurement to one cell', async () => {
    // A hidden or unmeasured container reports 0; resizing the PTY to 0 rows
    // would corrupt the remote screen.
    const seams = testSeams();
    const io = new SessionIoChannel('a1', 's1', seams.overrides);
    io.setSize(0, -5);

    io.channel.connect();
    await settle();
    seams.sockets[0]?.accept();

    expect(byteFrame(seams.sockets[0]?.sent[0])).toEqual([0x72, 0, 1, 0, 1]);
  });

  it('sends nothing on open before the first measurement', async () => {
    const seams = testSeams();
    const io = new SessionIoChannel('a1', 's1', seams.overrides);

    io.channel.connect();
    await settle();
    seams.sockets[0]?.accept();

    expect(seams.sockets[0]?.sent).toEqual([]);
  });
});

describe('AutoScanChannel', () => {
  it('subscribes from the live cursor on every open', async () => {
    // Android re-sends the map captured at connect time and resumes from a
    // stale mark; the cursor is rebuilt here so a reconnect resumes at the true
    // high-water mark.
    const seams = testSeams();
    const scan = new AutoScanChannel('a1', {
      subscribe: ['proj-a'],
      resumeFromSeq: { 'proj-a': 4n },
      ...seams.overrides,
    });

    scan.channel.connect();
    await settle();
    seams.sockets[0]?.accept();
    scan.cursor.admit('proj-a', 9n);
    seams.sockets[0]?.serverClose(1006);
    seams.runReconnect();
    await settle();
    seams.sockets[1]?.accept();

    expect(textFrame(seams.sockets[0]?.sent[0])).toEqual({
      subscribe: ['proj-a'],
      resume_from_seq: { 'proj-a': 4 },
    });
    expect(textFrame(seams.sockets[1]?.sent[0])).toEqual({
      subscribe: ['proj-a'],
      resume_from_seq: { 'proj-a': 9 },
    });
  });

  it('replaces the subscription set on a live socket', async () => {
    const seams = testSeams();
    const scan = new AutoScanChannel('a1', { subscribe: ['proj-a'], ...seams.overrides });

    scan.channel.connect();
    await settle();
    seams.sockets[0]?.accept();
    scan.setSubscribe(['proj-b', 'proj-c']);

    expect(textFrame(seams.sockets[0]?.sent[1])).toEqual({
      subscribe: ['proj-b', 'proj-c'],
      resume_from_seq: {},
    });
  });
});

describe('identifier handling in channel URLs', () => {
  it('percent-encodes ids so a stray character cannot reshape the URL', async () => {
    // `createAgentClient` already encodes the same identifier for the REST base;
    // a `#` left raw here would truncate the URL before `?token=`, producing a
    // permanent 4001 on that channel.
    const seams = testSeams();

    new SessionIoChannel('a/b', 's#1', seams.overrides).channel.connect();
    await settle();

    expect(seams.sockets[0]?.url).toBe(
      `${wsOrigin()}/relay/ws/session/a%2Fb/s%231?token=agent-jwt`,
    );
  });

  it('leaves an ordinary id byte-identical to the golden mapping', async () => {
    const seams = testSeams();

    createAgentEventsChannel('A1', seams.overrides).connect();
    await settle();

    expect(seams.sockets[0]?.url).toBe(`${wsOrigin()}/relay/ws/events/A1?token=agent-jwt`);
  });
});

describe('channel teardown', () => {
  it('closes the socket and forgets the store entry on dispose', async () => {
    const seams = testSeams();
    const session = new SessionIoChannel('A1', 's1', seams.overrides);
    session.channel.connect();
    await settle();
    seams.sockets[0]?.accept();
    expect(useConnectionStore.getState().channels['session:A1:s1']?.status).toBe('connected');

    session.dispose();

    expect(seams.sockets[0]?.readyState).toBe(3);
    expect(useConnectionStore.getState().channels['session:A1:s1']).toBeUndefined();
  });

  it('disposes an auto-scan channel the same way', async () => {
    const seams = testSeams();
    const autoscan = new AutoScanChannel('A1', { ...seams.overrides });
    autoscan.channel.connect();
    await settle();
    seams.sockets[0]?.accept();

    autoscan.dispose();

    expect(useConnectionStore.getState().channels['autoscan:A1']).toBeUndefined();
  });
});

describe('disposable channel registry', () => {
  it('lets a global teardown reach a channel its owner constructed', async () => {
    // stopAllRealtime() drains this registry; without it a PTY socket would
    // outlive the session that authorised it, since the relay only checks
    // credentials at handshake.
    const seams = testSeams();
    const session = new SessionIoChannel('A1', 's1', seams.overrides);
    session.channel.connect();
    await settle();
    seams.sockets[0]?.accept();

    disposeAllChannels();

    expect(seams.sockets[0]?.readyState).toBe(3);
    expect(useConnectionStore.getState().channels['session:A1:s1']).toBeUndefined();
  });

  it('does not re-dispose a channel that already disposed itself', () => {
    const seams = testSeams();
    const session = new SessionIoChannel('A1', 's2', seams.overrides);

    session.dispose();

    expect(() => {
      disposeAllChannels();
    }).not.toThrow();
  });
});

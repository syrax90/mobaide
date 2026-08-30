/**
 * The session socket's half of the resume sweep.
 *
 * `resumeOnFocus` re-opens the events channels after a network transition or a
 * long hide; the session socket was left out, so a laptop waking from sleep
 * could sit on a half-open one — and a session the relay closed as desynced
 * would wait out the backoff before the ring replay that repairs its screen.
 * The three guards matter as much as the reconnect: an unmounted model, a live
 * one and a suspended one all belong to someone else.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  fakeScheduler,
  recordingSocketFactory,
  settle,
  type FakeSocket,
} from '../realtime/__testing__/fakeSocket';
import { fakeEventTarget } from '../realtime/__testing__/fakeEventTarget';
import { TERMINAL_FRAME_TYPE } from '../protocol/terminalCodec';
import { useConnectionStore } from '../realtime/connectionStore';
import { observeResume } from '../realtime/resumeOnFocus';

vi.mock('../auth/agentTokens', () => ({
  ensureAgentToken: () => Promise.resolve('agent-jwt'),
  clearAgentToken: () => undefined,
}));

const { SessionIoChannel, sessionChannelName } = await import('../realtime/channels');
const { TerminalSessionModel } = await import('./sessionModel');
const { observeTabVisibility } = await import('../realtime/tabVisibility');
const { clearMountedTerminal, setMountedTerminal } = await import('./watchedTerminal');

type Model = InstanceType<typeof TerminalSessionModel>;

const KEY = sessionChannelName('a1', 's1');

const utf8 = new TextEncoder();
const outputFrame = (body: string): Uint8Array =>
  Uint8Array.from([TERMINAL_FRAME_TYPE.output, ...utf8.encode(body)]);

const live: Model[] = [];
const teardowns: (() => void)[] = [];

afterEach(() => {
  for (const model of live.splice(0)) model.dispose();
  for (const teardown of teardowns.splice(0)) teardown();
  clearMountedTerminal(KEY);
  useConnectionStore.setState({ channels: {} });
  vi.restoreAllMocks();
});

interface Harness {
  socket: FakeSocket;
  sockets: FakeSocket[];
  /** A real network transition — the trigger that needs no clock. */
  goOnline: () => void;
  hide: () => void;
  deliverOutput: (body: string) => void;
  view: () => ReturnType<Model['state']['getState']>;
}

/** A mounted model on an accepted socket that has not yet seen a frame, so
 * `connection` is still `'connecting'` — the state a resume must repair. */
async function harness(): Promise<Harness> {
  const { sockets, socketFactory } = recordingSocketFactory();
  const doc = fakeEventTarget();
  const win = fakeEventTarget();

  teardowns.push(observeTabVisibility(doc));
  teardowns.push(observeResume(() => 0, win, doc));
  setMountedTerminal(KEY);

  const channel = new SessionIoChannel('a1', 's1', {
    scheduler: fakeScheduler().scheduler,
    socketFactory,
  });
  const model = new TerminalSessionModel('a1', 's1', {
    scheduler: fakeScheduler().scheduler,
    channel,
    now: () => 0,
    suspendPolicy: ({ bytes }) => bytes >= 4,
  });
  live.push(model);

  await settle();
  const socket = sockets[0];
  if (socket === undefined) throw new Error('no socket was opened');
  socket.accept();

  return {
    socket,
    sockets,
    goOnline: () => win.fire('online'),
    hide: () => {
      doc.hidden = true;
      doc.fire('visibilitychange');
    },
    deliverOutput: (body: string) =>
      sockets[sockets.length - 1]?.deliver(outputFrame(body).buffer),
    view: () => model.state.getState(),
  };
}

describe('TerminalSessionModel resume reconnect', () => {
  it('re-opens the socket of a mounted session that is not live', async () => {
    const h = await harness();
    expect(h.view().connection).toBe('connecting');

    h.goOnline();
    await settle();

    expect(h.sockets).toHaveLength(2);
    expect(h.socket.clientCloses).toEqual([1000]);
  });

  it('leaves a live socket alone', async () => {
    // Nothing is wrong with it, and a reconnect would cost a whole ring replay.
    const h = await harness();
    h.deliverOutput('hello');
    expect(h.view().connection).toBe('live');

    h.goOnline();
    await settle();

    expect(h.sockets).toHaveLength(1);
    expect(h.socket.clientCloses).toEqual([]);
  });

  it('leaves an unmounted session alone', async () => {
    // `MAX_RETAINED` keeps three models attached while the route renders one;
    // the two off screen have nothing to repair.
    const h = await harness();
    clearMountedTerminal(KEY);

    h.goOnline();
    await settle();

    expect(h.sockets).toHaveLength(1);
  });

  it('leaves a suspended session to the watch gate', async () => {
    // The governor detached this one on purpose and owns its reopen; racing it
    // here would open a socket it is about to replace.
    const h = await harness();
    h.hide();
    h.deliverOutput('spending');
    expect(h.view().suspended).toBe(true);
    const closesBefore = h.socket.clientCloses.length;

    h.goOnline();
    await settle();

    expect(h.sockets).toHaveLength(1);
    expect(h.socket.clientCloses).toHaveLength(closesBefore);
  });
});

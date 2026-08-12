/**
 * The unwatched-session governor, from the model's side.
 *
 * `terminalSuspend.test` owns the thresholds; this file owns the wiring, so the
 * policy is injected and returns on a handful of bytes rather than the real
 * 4 MiB — pushing a ring through the emulator per case would make the suite
 * unusable and would test xterm rather than this.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  fakeScheduler,
  recordingSocketFactory,
  settle,
  type FakeSocket,
} from '../realtime/__testing__/fakeSocket';
import { WS_CLOSE } from '../protocol/closeCodes';
import { TERMINAL_FRAME_TYPE } from '../protocol/terminalCodec';
import { overlayMessage } from '../routes/terminal/ConnectionOverlay';
import { useConnectionStore } from '../realtime/connectionStore';
import { fakeEventTarget } from '../realtime/__testing__/fakeEventTarget';
import { shouldSuspend } from './terminalSuspend';

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

interface Harness {
  model: Model;
  socket: FakeSocket;
  sockets: FakeSocket[];
  hide: () => void;
  show: () => void;
  advance: (ms: number) => void;
  deliverOutput: (body: string) => void;
  deliverFrame: (frame: Uint8Array) => void;
  view: () => ReturnType<Model['state']['getState']>;
}

const live: Model[] = [];
const teardowns: (() => void)[] = [];

afterEach(() => {
  for (const model of live.splice(0)) model.dispose();
  for (const teardown of teardowns.splice(0)) teardown();
  clearMountedTerminal(KEY);
  useConnectionStore.setState({ channels: {} });
  vi.restoreAllMocks();
});

/**
 * A watched, live model: the tab is visible and this terminal is the mounted
 * one, which is the only state in which the governor is disarmed.
 *
 * `suspendPolicy` fires at four bytes of unwatched spend and no grace, so a
 * case that wants a suspend delivers one frame and a case that does not stays
 * under it.
 */
async function harness(
  policy: (spend: { unwatchedMs: number; bytes: number }) => boolean = ({ bytes }) =>
    bytes >= 4,
): Promise<Harness> {
  const { sockets, socketFactory } = recordingSocketFactory();
  const channelTimers = fakeScheduler();
  const modelTimers = fakeScheduler();
  const doc = fakeEventTarget();
  let clock = 0;

  teardowns.push(observeTabVisibility(doc));
  // Before the model is built: its constructor reads the pointer to decide
  // whether it starts watched.
  setMountedTerminal(KEY);

  const channel = new SessionIoChannel('a1', 's1', {
    scheduler: channelTimers.scheduler,
    socketFactory,
  });
  const model = new TerminalSessionModel('a1', 's1', {
    scheduler: modelTimers.scheduler,
    channel,
    now: () => clock,
    suspendPolicy: policy,
  });
  live.push(model);

  await settle();
  const socket = sockets[0];
  if (socket === undefined) throw new Error('no socket was opened');
  socket.accept();

  return {
    model,
    socket,
    sockets,
    hide: () => {
      doc.hidden = true;
      doc.fire('visibilitychange');
    },
    show: () => {
      doc.hidden = false;
      doc.fire('visibilitychange');
    },
    advance: (ms: number) => {
      clock += ms;
    },
    deliverOutput: (body: string) => {
      sockets[sockets.length - 1]?.deliver(outputFrame(body).buffer);
    },
    deliverFrame: (frame: Uint8Array) => {
      sockets[sockets.length - 1]?.deliver(frame.buffer);
    },
    view: () => model.state.getState(),
  };
}

describe('TerminalSessionModel unwatched-session suspension', () => {
  it('holds the socket open while the session is on screen and the tab is visible', async () => {
    const h = await harness();

    h.deliverOutput('plenty of output');

    expect(h.socket.clientCloses).toEqual([]);
    expect(h.view().suspended).toBe(false);
  });

  it('holds the socket open for a hidden session that spends nothing', async () => {
    // The byte term, which is what makes ordinary alt-tabbing free: no spend,
    // no suspend, and therefore no replay to pay for on return.
    const h = await harness();

    h.hide();
    h.advance(60_000);

    expect(h.socket.clientCloses).toEqual([]);
    expect(h.view().suspended).toBe(false);
  });

  it('closes the socket once a hidden session spends past the policy', async () => {
    const h = await harness();

    h.hide();
    h.deliverOutput('spending');

    expect(h.socket.clientCloses).toEqual([WS_CLOSE.normal]);
    expect(h.view().suspended).toBe(true);
  });

  it('closes the socket for an off-screen session even while the tab is visible', async () => {
    // `MAX_RETAINED` keeps three models attached and the route renders one, so
    // an unmounted model is as unread as anything in a hidden tab.
    const h = await harness();

    clearMountedTerminal(KEY);
    h.deliverOutput('spending');

    expect(h.socket.clientCloses).toEqual([WS_CLOSE.normal]);
    expect(h.view().suspended).toBe(true);
  });

  it('does not charge output that arrived while the session was being read', async () => {
    // The counter re-baselines on the transition, or a long-lived session would
    // carry its whole history into the first hide and suspend instantly.
    const h = await harness();

    h.deliverOutput('watched output');
    h.hide();

    expect(h.socket.clientCloses).toEqual([]);
    expect(h.view().suspended).toBe(false);
  });

  it('shuts the stdin gate so a suspended terminal cannot swallow keystrokes', async () => {
    // `disconnect()` deafens the socket before closing it, so no `disconnected`
    // event ever arrives. Without the model driving the replay state by hand it
    // would stay `live`, leave `disableStdin` false, and drop every keystroke
    // after xterm had already snapped the viewport and cleared the selection.
    const h = await harness();
    h.deliverFrame(Uint8Array.from([TERMINAL_FRAME_TYPE.state, ...utf8.encode('{"status":"running"}')]));

    h.hide();
    h.deliverOutput('spending');

    expect(h.view().suspended).toBe(true);
    expect(h.model.term.options.disableStdin).toBe(true);
    expect(h.model.pasteText('x')).toBe(false);
  });

  it('reads as paused rather than disconnected, matching Android and iOS', async () => {
    const h = await harness();

    h.hide();
    h.deliverOutput('spending');

    expect(h.view().channelStopped).toBe(false);
    expect(overlayMessage(h.view())).toBe('Paused');
  });

  it('suppresses the button even on a channel that had already given up', async () => {
    // `connectionStore.sameState` swallows an identical `{stopped, 0, null}`
    // write, so the store subscription alone never fires here and the button
    // would survive the suspend.
    const h = await harness();
    h.socket.serverClose(WS_CLOSE.adminRevoked);
    await settle();

    expect(h.view().channelStopped).toBe(true);

    h.model.suspend();

    expect(h.view().channelStopped).toBe(false);
  });

  it('still reports a real failure as Disconnected', async () => {
    const h = await harness();

    h.socket.serverClose(WS_CLOSE.adminRevoked);
    await settle();

    expect(h.view().suspended).toBe(false);
    expect(overlayMessage(h.view())).toBe('Disconnected');
  });

  it('reopens the socket the moment the session is read again', async () => {
    const h = await harness();
    h.hide();
    h.deliverOutput('spending');

    h.show();
    await settle();

    expect(h.sockets).toHaveLength(2);
    expect(h.view().suspended).toBe(false);
    expect(h.view().channelStopped).toBe(false);
  });

  it('reopens the socket when the user navigates back to an off-screen session', async () => {
    const h = await harness();
    clearMountedTerminal(KEY);
    h.deliverOutput('spending');

    setMountedTerminal(KEY);
    await settle();

    expect(h.sockets).toHaveLength(2);
    expect(h.view().suspended).toBe(false);
  });

  it('re-asserts the measured grid on the reopened socket', async () => {
    // A resumed PTY that kept the 24x80 it was spawned with would paint a
    // full-screen TUI into the top 24 rows.
    const h = await harness();
    h.model.term.resize(120, 40);
    h.hide();
    h.deliverOutput('spending');

    h.show();
    await settle();
    const reopened = h.sockets[1];
    if (reopened === undefined) throw new Error('no socket was reopened');
    reopened.accept();

    const resizes = reopened.sent.filter(
      (frame): frame is Uint8Array =>
        frame instanceof Uint8Array && frame[0] === TERMINAL_FRAME_TYPE.resize,
    );
    expect(resizes.length).toBeGreaterThan(0);
  });

  it('never suspends in the middle of a replay', async () => {
    // Tearing one down wastes bytes already paid for and strands
    // `replayPainting`, which only an `'e'` this span will never send clears.
    const h = await harness();
    h.hide();

    h.deliverFrame(Uint8Array.from([TERMINAL_FRAME_TYPE.replayStart]));
    h.deliverOutput('replayed history');

    expect(h.socket.clientCloses).toEqual([]);
    expect(h.view().suspended).toBe(false);
  });

  it('does not charge the replay it just paid for', async () => {
    // Otherwise a reconnect that lands while still unwatched pays a full ring,
    // is re-suspended by its own replay, and loops.
    const h = await harness(({ bytes }) => bytes >= 12);
    h.hide();

    h.deliverFrame(Uint8Array.from([TERMINAL_FRAME_TYPE.replayStart]));
    h.deliverOutput('a long replayed span');
    h.deliverFrame(Uint8Array.from([TERMINAL_FRAME_TYPE.replayEnd]));
    h.deliverOutput('tiny');

    expect(h.socket.clientCloses).toEqual([]);
    expect(h.view().suspended).toBe(false);
  });

  it('lets a user-driven retry outrank the governor', async () => {
    // Leaving the latch set would pin the byte counter at its early return, so
    // the session could never be suspended again.
    const h = await harness();
    h.hide();
    h.deliverOutput('spending');

    h.model.retryConnection();
    await settle();

    expect(h.view().suspended).toBe(false);
    expect(h.sockets).toHaveLength(2);

    h.sockets[1]?.accept();
    h.deliverOutput('spending again');

    expect(h.view().suspended).toBe(true);
  });

  it('disposes a suspended model without leaving a connection-store entry', async () => {
    const h = await harness();
    h.hide();
    h.deliverOutput('spending');

    h.model.dispose();
    live.splice(live.indexOf(h.model), 1);

    expect(useConnectionStore.getState().channels[KEY]).toBeUndefined();
    expect(h.sockets).toHaveLength(1);
  });

  it('leaves a modest spend alone under the shipped policy', async () => {
    // The shipped policy, not the injected stub: a few hundred bytes is nowhere
    // near a ring, so the real thresholds must not suspend on it.
    const h = await harness(shouldSuspend);

    h.hide();
    h.advance(120_000);
    h.deliverOutput('x'.repeat(512));

    expect(h.socket.clientCloses).toEqual([]);
    expect(h.view().suspended).toBe(false);
  });
});

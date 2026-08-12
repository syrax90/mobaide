import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  fakeScheduler,
  recordingSocketFactory,
  settle,
  type FakeSocket,
} from '../realtime/__testing__/fakeSocket';
import { encodeTerminalFrame, TERMINAL_FRAME_TYPE } from '../protocol/terminalCodec';
import { useToastStore } from '../stores/toastStore';

// Same credential-seam mock as `sessionModel.test.ts` — everything else in the
// channel graph is the real thing.
vi.mock('../auth/agentTokens', () => ({
  ensureAgentToken: () => Promise.resolve('agent-jwt'),
  clearAgentToken: () => undefined,
}));

// The parked-claim tests call `attach()`, and jsdom ships no `ResizeObserver`
// for it to install. Same inert stand-in as `TerminalScreen.test.tsx`; the
// host stays zero-sized so `open()` never runs and no layout is touched.
vi.stubGlobal(
  'ResizeObserver',
  class {
    observe = (): void => undefined;
    unobserve = (): void => undefined;
    disconnect = (): void => undefined;
  },
);

const { SessionIoChannel } = await import('../realtime/channels');
const { TerminalSessionModel } = await import('./sessionModel');

type Model = InstanceType<typeof TerminalSessionModel>;

const live: Model[] = [];

afterEach(() => {
  for (const model of live.splice(0)) model.dispose();
  useToastStore.setState({ toasts: [] });
  vi.restoreAllMocks();
});

interface Harness {
  model: Model;
  socket: FakeSocket;
  sockets: FakeSocket[];
  /** Fire the next armed model timer (resize debounce). */
  flushResize: () => number;
  /** Fire the next armed channel timer (reconnect backoff). */
  flushChannel: () => number;
  resizeFrames: (of?: FakeSocket) => number[][];
  deliverFrame: (frame: Uint8Array) => void;
}

async function harness(): Promise<Harness> {
  const { sockets, socketFactory } = recordingSocketFactory();
  const channelTimers = fakeScheduler();
  const modelTimers = fakeScheduler();

  const channel = new SessionIoChannel('a1', 's1', {
    scheduler: channelTimers.scheduler,
    socketFactory,
  });
  const model = new TerminalSessionModel('a1', 's1', {
    scheduler: modelTimers.scheduler,
    channel,
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
    flushResize: () => modelTimers.runNext(),
    flushChannel: () => channelTimers.runNext(),
    resizeFrames: (of?: FakeSocket) =>
      (of ?? socket).sent
        .filter(
          (frame): frame is Uint8Array =>
            frame instanceof Uint8Array &&
            frame[0] === TERMINAL_FRAME_TYPE.resize,
        )
        .map((frame) => [...frame]),
    deliverFrame: (frame: Uint8Array) => {
      socket.deliver(frame.buffer);
    },
  };
}

const utf8 = new TextEncoder();

const resizeFrame = (rows: number, cols: number): number[] => [
  ...encodeTerminalFrame({ kind: 'resize', rows, cols }),
];

const stateFrame = (status: string): Uint8Array =>
  Uint8Array.from([
    TERMINAL_FRAME_TYPE.state,
    ...utf8.encode(JSON.stringify({ status })),
  ]);

const winsizeFrame = (rows: number, cols: number, clients: number): Uint8Array =>
  Uint8Array.from([
    TERMINAL_FRAME_TYPE.winsize,
    rows >> 8, rows & 0xff,
    cols >> 8, cols & 0xff,
    clients >> 8, clients & 0xff,
  ]);

const outputFrame = (text: string): Uint8Array =>
  Uint8Array.from([TERMINAL_FRAME_TYPE.output, ...utf8.encode(text)]);

/** Solo model with a cached natural grid of 40x120 (one `r` frame sent). */
async function soloWithCachedGrid(): Promise<Harness> {
  const context = await harness();
  context.deliverFrame(stateFrame('running'));
  context.deliverFrame(winsizeFrame(24, 80, 1));
  context.model.term.resize(120, 40);
  context.flushResize();
  expect(context.resizeFrames()).toEqual([resizeFrame(40, 120)]);
  return context;
}

/**
 * Follower whose 400 ms focus claim has already elapsed against a closed send
 * gate (`stopped` status): the claim is parked, waiting on the gate. The one
 * armed model timer was the focus takeover, so `flushResize` fires it.
 */
async function parkedFocusClaim(): Promise<Harness> {
  const context = await soloWithCachedGrid();
  context.deliverFrame(winsizeFrame(50, 132, 2));
  expect(context.model.state.getState().sizeAuthority).toBe('follower');
  context.deliverFrame(stateFrame('stopped'));
  vi.spyOn(document, 'hasFocus').mockReturnValue(true);
  // Attaching in a focused tab is the focus edge that arms the takeover.
  context.model.attach(document.createElement('div'));
  context.flushResize();
  expect(context.model.state.getState().sizeAuthority).toBe('follower');
  return context;
}

describe('TerminalSessionModel size arbitration (proto-v8)', () => {
  it('starts as a follower: no size leaves before the role is resolved', async () => {
    // A fresh model attaching to a session another client is driving must
    // adopt, not steal — so layout passes stay local until a `w` (or the
    // v7 replay-end fallback) decides the role.
    const { model, resizeFrames } = await harness();
    expect(model.state.getState().sizeAuthority).toBe('follower');

    model.term.resize(120, 40);

    expect(resizeFrames()).toEqual([]);
  });

  it('a count of one makes it solo and layout resizes flow again', async () => {
    const { model, deliverFrame, flushResize, resizeFrames } = await harness();
    deliverFrame(winsizeFrame(24, 80, 1));
    expect(model.state.getState().sizeAuthority).toBe('solo');
    expect(resizeFrames()).toEqual([]); // no cached grid yet — nothing to assert

    model.term.resize(120, 40);
    flushResize();

    expect(resizeFrames()).toEqual([resizeFrame(40, 120)]);
  });

  it('solo re-asserts the cached grid when the agent reports a different one', async () => {
    // Covers "other client detached, its grid lingers on the PTY" and the
    // fresh 24x80 PTY: the reclaim happens immediately, no debounce.
    const { deliverFrame, resizeFrames } = await soloWithCachedGrid();

    deliverFrame(winsizeFrame(24, 80, 1));

    expect(resizeFrames()).toEqual([resizeFrame(40, 120), resizeFrame(40, 120)]);
  });

  it('stays silent when the solo report matches the cached grid', async () => {
    const { deliverFrame, resizeFrames } = await soloWithCachedGrid();

    deliverFrame(winsizeFrame(40, 120, 1));

    expect(resizeFrames()).toEqual([resizeFrame(40, 120)]);
  });

  it('recognizes its own size echoed back and becomes owner', async () => {
    const { model, deliverFrame, resizeFrames } = await soloWithCachedGrid();

    deliverFrame(winsizeFrame(40, 120, 2));

    expect(model.state.getState().sizeAuthority).toBe('owner');
    expect(model.state.getState().attachedClients).toBe(2);
    expect(resizeFrames()).toEqual([resizeFrame(40, 120)]);
  });

  it('a foreign grid demotes to follower and freezes every outbound resize', async () => {
    const { model, deliverFrame, resizeFrames } = await soloWithCachedGrid();

    deliverFrame(winsizeFrame(50, 132, 2));
    expect(model.state.getState().sizeAuthority).toBe('follower');

    model.term.resize(100, 30);

    expect(resizeFrames()).toEqual([resizeFrame(40, 120)]);
  });

  it('demotion cancels a resize debounce already armed', async () => {
    // The queued assert firing after the demotion would immediately fight
    // the client that just took the grid.
    const { model, deliverFrame, flushResize, resizeFrames } =
      await soloWithCachedGrid();
    model.term.resize(110, 35); // arms the 100 ms debounce

    deliverFrame(winsizeFrame(50, 132, 2));

    expect(flushResize).toThrowError(); // nothing left armed
    expect(resizeFrames()).toEqual([resizeFrame(40, 120)]);
  });

  it('typing takeover puts the frozen natural grid on the wire before the input', async () => {
    const { model, socket, deliverFrame } = await soloWithCachedGrid();
    deliverFrame(winsizeFrame(50, 132, 2));
    expect(model.state.getState().sizeAuthority).toBe('follower');
    const before = socket.sent.length;

    expect(model.pasteText('x')).toBe(true);

    const appended = socket.sent
      .slice(before)
      .filter((frame): frame is Uint8Array => frame instanceof Uint8Array);
    expect(appended[0]?.[0]).toBe(TERMINAL_FRAME_TYPE.resize);
    expect([...(appended[0] ?? [])]).toEqual(resizeFrame(40, 120));
    expect(appended[1]?.[0]).toBe(TERMINAL_FRAME_TYPE.input);
    expect(model.state.getState().sizeAuthority).toBe('owner');
  });

  it('reverts to solo at replay-end when no w arrived (v7 agent)', async () => {
    const { model, deliverFrame } = await harness();

    deliverFrame(Uint8Array.from([TERMINAL_FRAME_TYPE.replayStart]));
    deliverFrame(Uint8Array.from([TERMINAL_FRAME_TYPE.replayEnd]));

    expect(model.state.getState().sizeAuthority).toBe('solo');
  });

  it('keeps following at replay-end when a w did arrive on this socket', async () => {
    const { model, deliverFrame, resizeFrames } = await harness();
    deliverFrame(winsizeFrame(50, 132, 2));

    deliverFrame(Uint8Array.from([TERMINAL_FRAME_TYPE.replayStart]));
    deliverFrame(Uint8Array.from([TERMINAL_FRAME_TYPE.replayEnd]));

    expect(model.state.getState().sizeAuthority).toBe('follower');
    expect(resizeFrames()).toEqual([]);
  });

  it('suppresses the socket-open re-assert while following', async () => {
    // The reconnect resend (PR #409) must not fire a stale natural grid
    // ahead of the handshake's authoritative `w`.
    const context = await soloWithCachedGrid();
    context.deliverFrame(winsizeFrame(50, 132, 2));

    context.socket.serverClose(1006);
    context.flushChannel(); // reconnect backoff fires
    await settle();
    const reopened = context.sockets[1];
    if (reopened === undefined) throw new Error('no reconnect socket');
    reopened.accept();

    expect(context.resizeFrames(reopened)).toEqual([]);
  });

  it('re-asserts once the handshake re-promotes to solo after a reconnect', async () => {
    // A drop forfeits authority (round 2): `disconnected` demotes to
    // follower, so the counterpart to the suppression test above is no
    // longer "solo survives the drop" — it's "the socket-open resend stays
    // suppressed until the handshake's `w` re-decides the role, then fires."
    const context = await soloWithCachedGrid();

    context.socket.serverClose(1006);
    context.flushChannel();
    await settle();
    const reopened = context.sockets[1];
    if (reopened === undefined) throw new Error('no reconnect socket');
    reopened.accept();

    expect(context.resizeFrames(reopened)).toEqual([]);

    reopened.deliver(winsizeFrame(24, 80, 1).buffer);

    expect(context.model.state.getState().sizeAuthority).toBe('solo');
    expect(context.resizeFrames(reopened)).toEqual([resizeFrame(40, 120)]);
  });

  it('records a pre-w layout report and delivers it on the solo promotion', async () => {
    // The fresh-model state: no `w` yet, so no foreign grid is adopted — a
    // layout report IS this client's natural grid. It must be recorded (still
    // sending nothing) or the promotion below would find an empty cache, skip
    // its assert, and leave a fresh 24x80 PTY at the spawn size with nothing
    // left to re-fire the grid report.
    const { model, deliverFrame, resizeFrames } = await harness();
    model.term.resize(120, 40);
    expect(resizeFrames()).toEqual([]);

    deliverFrame(winsizeFrame(24, 80, 1));

    expect(model.state.getState().sizeAuthority).toBe('solo');
    expect(resizeFrames()).toEqual([resizeFrame(40, 120)]);
  });

  it('delivers the pre-w natural grid at the v7 replay-end promotion', async () => {
    // Same seed, v7 path: no `w` ever arrives, replay-end promotes to solo,
    // and the enterLive re-assert must find the recorded grid.
    const { model, deliverFrame, resizeFrames } = await harness();
    model.term.resize(120, 40);

    deliverFrame(Uint8Array.from([TERMINAL_FRAME_TYPE.replayStart]));
    deliverFrame(Uint8Array.from([TERMINAL_FRAME_TYPE.replayEnd]));

    expect(model.state.getState().sizeAuthority).toBe('solo');
    expect(resizeFrames()).toEqual([resizeFrame(40, 120)]);
  });

  it('keeps the cache frozen once a foreign grid is adopted', async () => {
    // Bounds the pre-w recording to the fresh-model state: the follower
    // solve's own `term.resize` echo lands in the same entry point and must
    // NOT overwrite the natural grid, or a takeover would assert the foreign
    // size and change nothing.
    const { model, socket, deliverFrame } = await soloWithCachedGrid();
    deliverFrame(winsizeFrame(50, 132, 2));
    model.term.resize(132, 50); // the follower solve applying the foreign grid
    const before = socket.sent.length;

    expect(model.pasteText('x')).toBe(true);

    const appended = socket.sent
      .slice(before)
      .filter((frame): frame is Uint8Array => frame instanceof Uint8Array);
    expect([...(appended[0] ?? [])]).toEqual(resizeFrame(40, 120));
  });

  it('an echo of the in-flight send keeps ownership and delivers the corrective', async () => {
    // Mid-drag the cache runs a debounce ahead of the wire, and the agent
    // echoes the frame that actually went out. Matching only the cache would
    // demote the very client that is actively resizing — and cancel its
    // pending corrective frame with the demotion. The own-echo branch instead
    // re-asserts the diverged cache immediately and drops the now-redundant
    // debounce, so exactly one corrective frame reaches the wire.
    const { model, deliverFrame, flushResize, resizeFrames } = await harness();
    deliverFrame(stateFrame('running'));
    deliverFrame(winsizeFrame(24, 80, 1));
    model.term.resize(118, 40);
    flushResize();
    expect(resizeFrames()).toEqual([resizeFrame(40, 118)]);
    model.term.resize(120, 40); // debounce still pending — the wire trails

    deliverFrame(winsizeFrame(40, 118, 2));

    expect(model.state.getState().sizeAuthority).toBe('owner');
    // The corrective went out at echo time; no debounce is left to flush.
    expect(resizeFrames()).toEqual([resizeFrame(40, 118), resizeFrame(40, 120)]);
    expect(() => flushResize()).toThrow('no reconnect was scheduled');
  });

  it('a matching grid this client never sent stays follower', async () => {
    // The own-echo owner branch is gated on an actual send (lastSent > 0):
    // the fresh-follower seeding records a merely-measured grid, and reading
    // that as "our size echoed back" would promote a fresh client whose
    // natural grid happens to equal the PTY's — it would then steal the size
    // on the next unattended window relayout.
    const { model, deliverFrame, resizeFrames } = await harness();
    model.term.resize(120, 40); // recorded as the natural grid; never sent

    deliverFrame(winsizeFrame(40, 120, 2));

    expect(model.state.getState().sizeAuthority).toBe('follower');
    expect(model.state.getState().attachedClients).toBe(2);
    expect(resizeFrames()).toEqual([]);
  });

  it('parks an elapsed focus claim on a closed gate and fires it once on reopen', async () => {
    const { model, deliverFrame, flushResize, resizeFrames } = await parkedFocusClaim();
    expect(resizeFrames()).toEqual([resizeFrame(40, 120)]); // parked: nothing fired

    deliverFrame(stateFrame('running')); // the send gate reopens

    expect(model.state.getState().sizeAuthority).toBe('owner');
    expect(resizeFrames()).toEqual([resizeFrame(40, 120), resizeFrame(40, 120)]);

    // The claim is consumed: a later gate sync must not take over again, and
    // no repeat takeover is left armed.
    deliverFrame(stateFrame('running'));
    expect(resizeFrames()).toEqual([resizeFrame(40, 120), resizeFrame(40, 120)]);
    expect(flushResize).toThrowError('no reconnect was scheduled');
  });

  it('a blur while the claim is parked drops it, so the reopening gate stays passive', async () => {
    const { model, deliverFrame, flushResize, resizeFrames } = await parkedFocusClaim();

    window.dispatchEvent(new Event('blur'));
    deliverFrame(stateFrame('running'));

    expect(model.state.getState().sizeAuthority).toBe('follower');
    expect(resizeFrames()).toEqual([resizeFrame(40, 120)]);
    expect(flushResize).toThrowError('no reconnect was scheduled');
  });

  it('a solo promotion while parked consumes the claim before the gate reopens', async () => {
    // The promotion's authority change clears the parked claim; the foreign
    // grid that follows demotes again, and the reopening gate must find no
    // stale claim to fire — only typing or a fresh focus edge may take over.
    const { model, deliverFrame, resizeFrames } = await parkedFocusClaim();

    deliverFrame(winsizeFrame(24, 80, 1)); // solo: clears the claim, re-asserts
    deliverFrame(winsizeFrame(50, 132, 2)); // foreign again before the reopen
    deliverFrame(stateFrame('running'));

    expect(model.state.getState().sizeAuthority).toBe('follower');
    expect(resizeFrames()).toEqual([resizeFrame(40, 120), resizeFrame(40, 120)]);
  });

  it('a foreign winsize inside the replay span decides the role like a pre-replay one', async () => {
    // The reducer must pass `w` through mid-replay in frame order: deferred
    // (or dropped) past `'e'`, the v7 fallback would see no `w` on this
    // socket and wrongly promote the follower to solo.
    const { model, deliverFrame, resizeFrames } = await harness();

    deliverFrame(Uint8Array.from([TERMINAL_FRAME_TYPE.replayStart]));
    deliverFrame(outputFrame('replayed history'));
    deliverFrame(winsizeFrame(50, 132, 2));
    deliverFrame(Uint8Array.from([TERMINAL_FRAME_TYPE.replayEnd]));

    expect(model.state.getState().sizeAuthority).toBe('follower');
    expect(resizeFrames()).toEqual([]);
  });

  it('a solo winsize inside the replay span promotes and delivers the natural grid', async () => {
    // Same decision as the pre-`b` position: promotion plus the immediate
    // mismatch assert, then the unconditional enterLive re-assert at
    // replay-end — two identical frames, none of them lost to replay state.
    const { model, deliverFrame, resizeFrames } = await harness();
    model.term.resize(120, 40); // recorded pre-w, nothing sent

    deliverFrame(Uint8Array.from([TERMINAL_FRAME_TYPE.replayStart]));
    deliverFrame(outputFrame('replayed history'));
    deliverFrame(winsizeFrame(24, 80, 1));
    deliverFrame(Uint8Array.from([TERMINAL_FRAME_TYPE.replayEnd]));

    expect(model.state.getState().sizeAuthority).toBe('solo');
    expect(resizeFrames()).toEqual([resizeFrame(40, 120), resizeFrame(40, 120)]);
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  fakeScheduler,
  recordingSocketFactory,
  settle,
  type FakeSocket,
} from '../realtime/__testing__/fakeSocket';
import { WS_CLOSE } from '../protocol/closeCodes';
import { encodeTerminalFrame, TERMINAL_FRAME_TYPE } from '../protocol/terminalCodec';
import { useToastStore } from '../stores/toastStore';
import { MAX_PASTE_BYTES } from './terminalPaste';
import { CLEAR_INPUT_LINE } from './terminalSubmit';

// The credential seams are the only part of the channel that would go to the
// network; the rest of the graph is the real thing, as in `channels.test.ts`.
vi.mock('../auth/agentTokens', () => ({
  ensureAgentToken: () => Promise.resolve('agent-jwt'),
  clearAgentToken: () => undefined,
}));

const { SessionIoChannel } = await import('../realtime/channels');
const { TerminalSessionModel } = await import('./sessionModel');

type Model = InstanceType<typeof TerminalSessionModel>;

const RESIZE_DEBOUNCE_MS = 100;

interface Harness {
  model: Model;
  socket: FakeSocket;
  /** Every socket the channel has built, oldest first. */
  sockets: FakeSocket[];
  /** Fire the armed resize debounce and report the delay it was armed with. */
  flushResize: () => number;
  /** How many resize timers are armed; a burst must never arm more than one. */
  pendingResizes: () => number;
  resizeFrames: () => number[][];
  deliver: (typeByte: number) => void;
  /** Play a whole server frame, payload included. */
  deliverFrame: (frame: Uint8Array) => void;
  /** Outbound frames of one type, in the order they reached the socket. */
  framesOfType: (typeByte: number) => Uint8Array[];
}

const live: Model[] = [];

afterEach(() => {
  for (const model of live.splice(0)) model.dispose();
  useToastStore.setState({ toasts: [] });
  vi.restoreAllMocks();
});

async function harness(): Promise<Harness> {
  const { sockets, socketFactory } = recordingSocketFactory();
  // Two clocks: the channel's drives reconnect backoff, the model's drives the
  // resize debounce. Sharing one would make "which timer fired" ambiguous.
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

  const framesOfType = (typeByte: number): Uint8Array[] =>
    socket.sent.filter(
      (frame): frame is Uint8Array =>
        frame instanceof Uint8Array && frame[0] === typeByte,
    );

  return {
    model,
    socket,
    sockets,
    flushResize: () => modelTimers.runNext(),
    pendingResizes: () => modelTimers.pending.length,
    resizeFrames: () =>
      framesOfType(TERMINAL_FRAME_TYPE.resize).map((frame) => [...frame]),
    deliver: (typeByte: number) => {
      socket.deliver(Uint8Array.from([typeByte]).buffer);
    },
    deliverFrame: (frame: Uint8Array) => {
      socket.deliver(frame.buffer);
    },
    framesOfType,
  };
}

const resizeFrame = (rows: number, cols: number): number[] => [
  ...encodeTerminalFrame({ kind: 'resize', rows, cols }),
];

const utf8 = new TextEncoder();

const frameOf = (typeByte: number, body: string): Uint8Array =>
  Uint8Array.from([typeByte, ...utf8.encode(body)]);

const outputFrame = (bytes: string): Uint8Array =>
  frameOf(TERMINAL_FRAME_TYPE.output, bytes);

const stateFrame = (status: string): Uint8Array =>
  frameOf(TERMINAL_FRAME_TYPE.state, JSON.stringify({ status }));

const payloadText = (frame: Uint8Array): string =>
  new TextDecoder().decode(frame.subarray(1));

/**
 * A model on an open socket with the process reported running — the state every
 * send gate needs before it will pass anything to the PTY. The `s` frame doubles
 * as the first inbound message, which is what upgrades the channel to live.
 */
async function liveRunning(): Promise<Harness> {
  const context = await harness();
  context.deliverFrame(stateFrame('running'));
  return context;
}

describe('TerminalSessionModel grid size', () => {
  it('sends nothing until the resize debounce fires', async () => {
    const { model, flushResize, resizeFrames } = await harness();

    model.term.resize(120, 40);
    expect(resizeFrames()).toEqual([]);

    expect(flushResize()).toBe(RESIZE_DEBOUNCE_MS);
    expect(resizeFrames()).toEqual([resizeFrame(40, 120)]);
  });

  it('collapses a burst of layout passes into one frame carrying the final grid', async () => {
    // A window drag produces a resize per animation frame; one `r` each would
    // flood the relay and re-wrap the PTY at every intermediate width. Each
    // pass has to *replace* the armed timer, not queue another one.
    const { model, flushResize, pendingResizes, resizeFrames } = await harness();

    model.term.resize(100, 30);
    model.term.resize(110, 35);
    model.term.resize(120, 40);

    expect(pendingResizes()).toBe(1);

    flushResize();

    expect(resizeFrames()).toEqual([resizeFrame(40, 120)]);
    expect(pendingResizes()).toBe(0);
  });

  it('re-asserts the cached grid when a socket goes live', async () => {
    // The agent spawns every session at 24x80 and never reports a size back,
    // so the client is the sole size authority and has to re-assert on every
    // connect — including the one that arrives with the replay marker.
    const { model, flushResize, resizeFrames, deliver } = await harness();
    model.term.resize(120, 40);
    flushResize();
    expect(resizeFrames()).toHaveLength(1);

    deliver(TERMINAL_FRAME_TYPE.replayStart);

    expect(resizeFrames()).toEqual([resizeFrame(40, 120), resizeFrame(40, 120)]);
  });

  it('re-asserts on a frame it could not decode', async () => {
    // The upgrade to live precedes the decode, exactly as it does on Android.
    const { model, flushResize, resizeFrames, deliver } = await harness();
    model.term.resize(120, 40);
    flushResize();

    deliver(0x00);

    expect(resizeFrames()).toHaveLength(2);
  });
});

describe('TerminalSessionModel connection state', () => {
  it('reports replaying between the markers and live once the span closes', async () => {
    const { model, deliver } = await harness();
    expect(model.state.getState().connection).toBe('connecting');

    deliver(TERMINAL_FRAME_TYPE.replayStart);
    expect(model.state.getState().connection).toBe('replaying');

    deliver(TERMINAL_FRAME_TYPE.replayEnd);
    expect(model.state.getState().connection).toBe('live');
  });

  it('goes back to connecting when the socket drops mid-replay', async () => {
    const { model, socket, deliver } = await harness();
    deliver(TERMINAL_FRAME_TYPE.replayStart);

    socket.serverClose(1006);

    expect(model.state.getState().connection).toBe('connecting');
  });

  it('keeps one span from lifting the overlay for the next one', async () => {
    // Stands in for a socket that dropped between `'e'` and the parser
    // draining: the next span's `'b'` arrives while the previous span's write
    // callbacks are still queued. Each span owns its own end marker, so the
    // wipe has to drop the old one — otherwise the stale flag is consumed by
    // the drain and reveals a screen carrying nothing but the wipe.
    const { model, deliverFrame, deliver } = await liveRunning();
    deliver(TERMINAL_FRAME_TYPE.replayStart);
    deliverFrame(outputFrame('scrollback from the first span'));
    deliver(TERMINAL_FRAME_TYPE.replayEnd);
    deliver(TERMINAL_FRAME_TYPE.replayStart);

    await settle();

    expect(model.state.getState().replayPainting).toBe(true);
    expect(model.state.getState().connection).toBe('replaying');
  });
});

describe('TerminalSessionModel send gate', () => {
  it('holds input back until the replayed history has been parsed', async () => {
    // `'e'` flips the connection to live synchronously but `term.write` is
    // asynchronous, so the parser is still chewing through replayed history
    // afterwards. A query sequence in that history — DECRQM for mode 2026,
    // XTVERSION, DECRQSS, OSC 4, none of which the agent's ring scrub strips —
    // would be answered after the gate opened and land in the user's prompt.
    const { model, deliverFrame, deliver, framesOfType } = await liveRunning();
    deliver(TERMINAL_FRAME_TYPE.replayStart);
    deliverFrame(outputFrame('replayed history'));
    deliver(TERMINAL_FRAME_TYPE.replayEnd);

    expect(model.state.getState().connection).toBe('live');
    model.pasteText('ls\r');
    expect(framesOfType(TERMINAL_FRAME_TYPE.input)).toEqual([]);

    await settle();
    model.pasteText('ls\r');

    expect(framesOfType(TERMINAL_FRAME_TYPE.input).map(payloadText)).toEqual(['ls\r']);
  });

  it('sends nothing while the process is not running', async () => {
    const { model, deliverFrame, framesOfType } = await harness();
    deliverFrame(stateFrame('stopped'));

    model.pasteText('ls\r');

    expect(framesOfType(TERMINAL_FRAME_TYPE.input)).toEqual([]);
  });

  it('holds a selection run back until the replayed history has been parsed', async () => {
    // Worse than the input case: the selection an `x` frame carries is read off
    // the buffer at the moment of the call, so between `'e'` and the parser
    // draining it is taken from a screen that is still half-reconstructed —
    // the frame would replay a command the user never selected.
    const { model, deliverFrame, deliver, framesOfType } = await liveRunning();
    vi.spyOn(model.term, 'getSelection').mockReturnValue('git status');
    deliver(TERMINAL_FRAME_TYPE.replayStart);
    deliverFrame(outputFrame('replayed history'));
    deliver(TERMINAL_FRAME_TYPE.replayEnd);

    expect(model.state.getState().connection).toBe('live');
    model.runSelection();
    expect(framesOfType(TERMINAL_FRAME_TYPE.execute)).toEqual([]);

    await settle();
    model.runSelection();

    expect(framesOfType(TERMINAL_FRAME_TYPE.execute).map(payloadText)).toEqual([
      'git status',
    ]);
  });
});

describe('TerminalSessionModel stdin gate', () => {
  // `disableStdin` mirrors `canSendBytes()` into the emulator, because xterm
  // acts on a keystroke before the model ever sees the bytes: `CoreService`
  // re-pins the viewport to the tail on user input — silently clearing the
  // unread badge — and `SelectionService` drops the selection on the same
  // signal. A key pressed while the gate is shut would therefore throw away the
  // scroll position and the selection of someone reading back a stopped
  // session, for input that goes nowhere. Refusing the key from
  // `attachCustomKeyEventHandler` instead would skip xterm's `preventDefault`
  // and leak Tab, Space and `/` to the browser.
  interface GateCase {
    name: string;
    reach: (context: Harness) => void | Promise<void>;
    disabled: boolean;
  }

  const goLiveRunning = (context: Harness): void => {
    context.deliverFrame(stateFrame('running'));
  };

  const replaySpan = (context: Harness): void => {
    context.deliver(TERMINAL_FRAME_TYPE.replayStart);
    context.deliverFrame(outputFrame('replayed history'));
    context.deliver(TERMINAL_FRAME_TYPE.replayEnd);
  };

  const cases: GateCase[] = [
    {
      name: 'shut before the first inbound frame',
      reach: () => undefined,
      disabled: true,
    },
    {
      name: 'open once the channel is live and the process is running',
      reach: goLiveRunning,
      disabled: false,
    },
    {
      name: 'shut again while a replay span is still in the parser',
      reach: (context) => {
        goLiveRunning(context);
        replaySpan(context);
      },
      disabled: true,
    },
    {
      name: 'open again once the replayed history has drained',
      // The drain is the only thing left to re-open it: `'e'` ran the gate while
      // the writes were still outstanding, so on a session that goes quiet after
      // its replay nothing else would arrive to re-run it and the keyboard would
      // stay dead until the next frame.
      reach: async (context) => {
        goLiveRunning(context);
        replaySpan(context);
        await settle();
      },
      disabled: false,
    },
    {
      name: 'shut when the socket drops',
      reach: (context) => {
        goLiveRunning(context);
        context.socket.serverClose(1006);
      },
      disabled: true,
    },
    {
      name: 'shut when the process is no longer running',
      reach: (context) => {
        goLiveRunning(context);
        context.deliverFrame(stateFrame('stopped'));
      },
      disabled: true,
    },
  ];

  it.each(cases)('$name', async ({ reach, disabled }) => {
    const context = await harness();

    await reach(context);

    expect(context.model.term.options.disableStdin).toBe(disabled);
  });
});

describe('TerminalSessionModel stopped channel', () => {
  it('re-opens a socket when the user retries a channel that gave up', async () => {
    // 4010 arrives verbatim on an already-open socket and is terminal on the
    // agent axis, so nothing re-arms the channel on its own — the screen would
    // stay dead until the tab is reloaded or the model evicted.
    const { model, socket, sockets } = await harness();

    socket.serverClose(WS_CLOSE.adminRevoked);

    expect(model.state.getState().channelStopped).toBe(true);
    expect(sockets).toHaveLength(1);

    model.retryConnection();
    await settle();

    expect(sockets).toHaveLength(2);
  });

  it('ignores a retry aimed at a model that was disposed under the button', async () => {
    // `stopAllRealtime()` runs on every session change, so a click landing after
    // disposal is ordinary; `connect()` would then open a socket owned by
    // nothing, outliving the model that was supposed to close it.
    const { model, socket, sockets } = await harness();
    socket.serverClose(WS_CLOSE.adminRevoked);
    model.dispose();

    model.retryConnection();
    await settle();

    expect(sockets).toHaveLength(1);
  });
});

describe('TerminalSessionModel write faults', () => {
  const discard = (): never => {
    throw new Error('write data discarded, use flow control to avoid losing data');
  };

  it('keeps a discarded live write from escaping into the socket handler', async () => {
    // `WriteBuffer.write` throws past its 50 MB backlog watermark, which a tab
    // backgrounded long enough for the browser to throttle the parse loop can
    // reach. Unguarded, that throw comes out of `socket.onmessage`.
    const { model, deliverFrame } = await liveRunning();
    const write = vi.spyOn(model.term, 'write').mockImplementation(discard);

    expect(() => {
      deliverFrame(outputFrame('live output'));
    }).not.toThrow();
    expect(write).toHaveBeenCalledTimes(1);
  });

  it('unwinds the replay counter a discarded write would otherwise strand', async () => {
    // `write` raises before queueing, so no callback ever arrives to balance
    // the increment — and a stranded counter now pins the send gate shut as
    // well as holding the overlay up.
    const { model, deliverFrame, deliver, framesOfType } = await liveRunning();
    vi.spyOn(model.term, 'write').mockImplementation(discard);

    deliver(TERMINAL_FRAME_TYPE.replayStart);
    deliverFrame(outputFrame('replayed history'));
    deliver(TERMINAL_FRAME_TYPE.replayEnd);

    expect(model.state.getState().replayPainting).toBe(false);
    model.pasteText('ls\r');
    expect(framesOfType(TERMINAL_FRAME_TYPE.input)).toHaveLength(1);
  });
});

describe('TerminalSessionModel outbound payload cap', () => {
  const toastKinds = (): string[] =>
    useToastStore.getState().toasts.map((toast) => toast.kind);

  it('sends the composer body behind the line clear and empties the field', async () => {
    const { model, framesOfType } = await liveRunning();
    model.setComposer('list the open PRs');

    model.submitComposer();

    expect(framesOfType(TERMINAL_FRAME_TYPE.submit).map(payloadText)).toEqual([
      `${CLEAR_INPUT_LINE}list the open PRs`,
    ]);
    expect(model.state.getState().composer).toBe('');
    expect(toastKinds()).toEqual([]);
  });

  it('refuses an oversized composer body and explains instead of sending it', async () => {
    // "Send to input" copies an arbitrarily large selection into the composer,
    // so this is reachable without an attacker. The relay forwards the frame to
    // the agent's tunnel unchunked and that tunnel is opened with
    // `max_size=4 MB`, so one oversized frame closes it with 1009 and drops
    // every session on that agent.
    const oversized = 'a'.repeat(MAX_PASTE_BYTES + 1);
    const { model, framesOfType } = await liveRunning();
    model.setComposer(oversized);

    model.submitComposer();

    expect(framesOfType(TERMINAL_FRAME_TYPE.submit)).toEqual([]);
    expect(toastKinds()).toEqual(['error']);
    // Kept in the field so the user can trim it rather than losing the text.
    expect(model.state.getState().composer).toBe(oversized);
  });
});

describe('TerminalSessionModel clipboard copy', () => {
  let settleWrite: (outcome: 'ok' | 'refused') => void;

  beforeEach(() => {
    // jsdom ships no `navigator.clipboard`; install one the test can hold open
    // for as long as it needs to interleave a disposal.
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: {
        writeText: () =>
          new Promise<void>((resolve, reject) => {
            settleWrite = (outcome) => {
              if (outcome === 'ok') resolve();
              else reject(new Error('NotAllowedError'));
            };
          }),
      },
    });
  });

  afterEach(() => {
    Reflect.deleteProperty(navigator, 'clipboard');
  });

  it('reports a refused write while the model is still alive', async () => {
    const { model } = await liveRunning();
    vi.spyOn(model.term, 'getSelection').mockReturnValue('copied text');

    const copying = model.copySelection();
    settleWrite('refused');
    await copying;

    expect(useToastStore.getState().toasts.map((toast) => toast.kind)).toEqual([
      'error',
    ]);
  });

  it('stays quiet when the model is disposed mid-write', async () => {
    // `stopAllRealtime()` fires on every session change, so a disposal landing
    // inside the clipboard await is ordinary. The continuation would otherwise
    // toast for, and reach into, a terminal that no longer exists.
    const { model } = await liveRunning();
    vi.spyOn(model.term, 'getSelection').mockReturnValue('copied text');

    const copying = model.copySelection();
    model.dispose();
    settleWrite('refused');
    await copying;

    expect(useToastStore.getState().toasts).toEqual([]);
  });
});

describe('TerminalSessionModel OSC 52 writes', () => {
  /** `ESC]52;c;<base64 "hello">BEL` — the write form, targeting the system selection. */
  const OSC52_WRITE = ']52;c;aGVsbG8=';

  beforeEach(() => {
    // Never settles. A provider that handed this promise back to `ClipboardAddon`
    // would leave xterm's OSC dispatcher suspended on it for the rest of the test,
    // which is exactly the failure being pinned.
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: () => new Promise<void>(() => undefined) },
    });
  });

  afterEach(() => {
    Reflect.deleteProperty(navigator, 'clipboard');
  });

  it('does not let a replayed OSC 52 write stall the parser', async () => {
    // `ClipboardAddon` returns the provider's result to the OSC dispatcher
    // whenever it is a promise, and xterm pauses the parser until that settles.
    // A paused parser fires no write callbacks, so `pendingReplayWrites` never
    // drains — and that counter holds up both the overlay and the send gate. A
    // program could otherwise pack the agent's 4 MiB ring with OSC 52 writes and
    // freeze the screen and the keyboard after every reconnect, with no way for
    // the user to type the poisoned bytes back out of the ring.
    const { model, deliverFrame, deliver, framesOfType } = await liveRunning();

    deliver(TERMINAL_FRAME_TYPE.replayStart);
    deliverFrame(outputFrame(OSC52_WRITE));
    deliver(TERMINAL_FRAME_TYPE.replayEnd);
    await settle();

    expect(model.state.getState().replayPainting).toBe(false);
    model.pasteText('ls\r');
    expect(framesOfType(TERMINAL_FRAME_TYPE.input).map(payloadText)).toEqual(['ls\r']);
  });
});

describe('TerminalSessionModel synchronized output guard', () => {
  /** DECSET/DECRST 2026. Never a raw 0x1B — repo convention. */
  const ARM_SYNC = '[?2026h';
  const DISARM_SYNC = '[?2026l';

  const clipboardWrites: string[] = [];

  const toastKinds = (): string[] =>
    useToastStore.getState().toasts.map((toast) => toast.kind);

  beforeEach(() => {
    clipboardWrites.length = 0;
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: {
        writeText: (text: string) => {
          clipboardWrites.push(text);
          return Promise.resolve();
        },
      },
    });
  });

  afterEach(() => {
    Reflect.deleteProperty(navigator, 'clipboard');
  });

  /**
   * A live session carrying a selection, with mode 2026 in the requested state.
   *
   * `getSelection` is stubbed because the real one reads `_selectionService`,
   * which xterm only builds inside `open()` — and jsdom has neither the canvas
   * metrics nor the WebGL context that needs.
   */
  async function selecting(mode: string): Promise<Harness> {
    const context = await liveRunning();
    vi.spyOn(context.model.term, 'getSelection').mockReturnValue('selected text');
    context.deliverFrame(outputFrame(mode));
    await settle();
    return context;
  }

  it('refuses a copy while the frame is still being painted', async () => {
    // While output is synchronized the buffer is ahead of what the user can
    // see, so a copy would hand back text that was never on screen. Both mobile
    // clients refuse the same read for the same reason.
    const { model } = await selecting(ARM_SYNC);

    await model.copySelection();

    expect(clipboardWrites).toEqual([]);
    expect(toastKinds()).toEqual(['info']);
  });

  it('refuses a send-to-composer while the frame is still being painted', async () => {
    const { model } = await selecting(ARM_SYNC);

    model.sendSelectionToComposer();

    expect(model.state.getState().composer).toBe('');
    expect(model.state.getState().composerVisible).toBe(false);
    expect(toastKinds()).toEqual(['info']);
  });

  it('refuses a run while the frame is still being painted', async () => {
    const { model, framesOfType } = await selecting(ARM_SYNC);

    model.runSelection();

    expect(framesOfType(TERMINAL_FRAME_TYPE.execute)).toEqual([]);
    expect(toastKinds()).toEqual(['info']);
  });

  it('lets the same three actions through once the program ends the frame', async () => {
    // Read at the instant of the action rather than cached in the view state:
    // xterm clears mode 2026 on a 1 s safety timeout inside `RenderService` with
    // no event of any kind, so a flag sampled on writes or selection changes has
    // a transition it can never observe and would strand the toolbar over a
    // screen that is actually current.
    const { model, deliverFrame, framesOfType } = await selecting(ARM_SYNC);
    model.runSelection();
    expect(framesOfType(TERMINAL_FRAME_TYPE.execute)).toEqual([]);

    deliverFrame(outputFrame(DISARM_SYNC));
    await settle();

    await model.copySelection();
    model.sendSelectionToComposer();
    model.runSelection();

    expect(clipboardWrites).toEqual(['selected text']);
    expect(model.state.getState().composer).toBe('selected text');
    expect(model.state.getState().composerVisible).toBe(true);
    expect(framesOfType(TERMINAL_FRAME_TYPE.execute).map(payloadText)).toEqual([
      'selected text',
    ]);
    // The one refusal above is all that ever spoke.
    expect(toastKinds()).toEqual(['info']);
  });

  /**
   * A live session carrying a selection whose replayed history is still queued
   * in the parser. `'e'` flips the connection to live synchronously while
   * `term.write` stays asynchronous, so the toolbar is reachable over a
   * half-reconstructed buffer; not calling `settle()` is what holds it there.
   */
  async function replayDraining(): Promise<Harness> {
    const context = await liveRunning();
    vi.spyOn(context.model.term, 'getSelection').mockReturnValue('selected text');
    context.deliver(TERMINAL_FRAME_TYPE.replayStart);
    context.deliverFrame(outputFrame('replayed history'));
    context.deliver(TERMINAL_FRAME_TYPE.replayEnd);
    return context;
  }

  it('refuses a copy while the replayed history is still being parsed', async () => {
    // The send gate held the `x` frame back from the start, but copy went
    // straight to the buffer: the user would have got text off a screen that was
    // still being rebuilt, on the system clipboard, with no way to tell.
    const { model } = await replayDraining();
    expect(model.state.getState().replayPainting).toBe(true);

    await model.copySelection();

    expect(clipboardWrites).toEqual([]);
    expect(toastKinds()).toEqual(['info']);
  });

  it('refuses a send-to-composer while the replayed history is still being parsed', async () => {
    const { model } = await replayDraining();

    model.sendSelectionToComposer();

    expect(model.state.getState().composer).toBe('');
    expect(model.state.getState().composerVisible).toBe(false);
    expect(toastKinds()).toEqual(['info']);
  });

  it('says why a run was refused mid-replay instead of dropping it silently', async () => {
    const { model, framesOfType } = await replayDraining();

    model.runSelection();

    expect(framesOfType(TERMINAL_FRAME_TYPE.execute)).toEqual([]);
    expect(toastKinds()).toEqual(['info']);
  });

  it('lets the same three actions through once the parser drains', async () => {
    const { model, framesOfType } = await replayDraining();

    await settle();
    await model.copySelection();
    model.sendSelectionToComposer();
    model.runSelection();

    expect(clipboardWrites).toEqual(['selected text']);
    expect(model.state.getState().composer).toBe('selected text');
    expect(framesOfType(TERMINAL_FRAME_TYPE.execute).map(payloadText)).toEqual([
      'selected text',
    ]);
    expect(toastKinds()).toEqual([]);
  });
});

describe('TerminalSessionModel selection focus', () => {
  /** Flipped by the one test that needs the browser to refuse the write. */
  let clipboardAllows = true;

  beforeEach(() => {
    clipboardAllows = true;
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: {
        writeText: () =>
          clipboardAllows
            ? Promise.resolve()
            : Promise.reject(new Error('NotAllowedError')),
      },
    });
  });

  afterEach(() => {
    Reflect.deleteProperty(navigator, 'clipboard');
  });

  /** A live session carrying a selection; `getSelection` stubbed as above. */
  async function selecting(): Promise<Harness> {
    const context = await liveRunning();
    vi.spyOn(context.model.term, 'getSelection').mockReturnValue('selected text');
    return context;
  }

  /**
   * Spied on the terminal rather than on `model.focus()`, because that is the
   * call the user feels. jsdom builds no textarea before `open()`, so the real
   * `Terminal.focus()` is a silent no-op and asserting on the DOM would prove
   * nothing either way.
   */
  const spyFocus = (model: Model) => vi.spyOn(model.term, 'focus');

  it('returns focus to the grid after a run', async () => {
    // `SelectionService.clearSelection()` fires `onSelectionChange`
    // unconditionally, which drops `hasSelection`, which unmounts the toolbar —
    // taking the button the user just pressed out of the DOM. Focus falls to
    // `document.body` and the keyboard stops reaching the PTY until the grid is
    // clicked again, immediately after a core action.
    const { model, framesOfType } = await selecting();
    const focus = spyFocus(model);

    model.runSelection();

    expect(framesOfType(TERMINAL_FRAME_TYPE.execute)).toHaveLength(1);
    expect(focus).toHaveBeenCalledTimes(1);
  });

  it('returns focus to the grid after a copy', async () => {
    const { model } = await selecting();
    const focus = spyFocus(model);

    await model.copySelection();

    expect(focus).toHaveBeenCalledTimes(1);
  });

  it('leaves focus alone when the clipboard write was refused', async () => {
    // The refusal returns ahead of `clearSelection`, so the toolbar is still
    // mounted, still holds the focused button, and still offers the retry.
    // Yanking focus to the grid there would take the retry off the keyboard.
    clipboardAllows = false;
    const { model } = await selecting();
    const focus = spyFocus(model);

    await model.copySelection();

    expect(useToastStore.getState().toasts.map((toast) => toast.kind)).toEqual([
      'error',
    ]);
    expect(focus).not.toHaveBeenCalled();
  });

  it('leaves the grid unfocused when the selection is sent to the composer', async () => {
    // Deliberately unlike the other two: this action puts a text field on
    // screen, so focusing the grid would feed the user's next keystrokes to the
    // PTY instead of the message they opened the composer to write. Pinned so a
    // later pass at making the three selection actions symmetric has to
    // confront it.
    const { model } = await selecting();
    const focus = spyFocus(model);

    model.sendSelectionToComposer();

    expect(model.state.getState().composerVisible).toBe(true);
    expect(focus).not.toHaveBeenCalled();
  });
});

describe('TerminalSessionModel scrollbar geometry', () => {
  const ALT_SCREEN_ON = '[?1049h';
  const ALT_SCREEN_OFF = '[?1049l';

  /** Comfortably past the 24-row default grid, so a real scrollback exists. */
  const lines = (count: number): string =>
    Array.from({ length: count }, (_, index) => `line ${String(index)}`).join('\r\n');

  async function withScrollback(): Promise<Harness> {
    const context = await liveRunning();
    context.deliverFrame(outputFrame(lines(80)));
    await settle();
    return context;
  }

  it('offers nothing to scroll until the buffer outgrows the grid', async () => {
    const { model } = await liveRunning();

    expect(model.scrollbar.getState()).toEqual({
      scrollable: false,
      size: 1,
      offset: 1,
    });
  });

  it('shrinks the thumb and parks it at the tail while pinned', async () => {
    const { model } = await withScrollback();
    const view = model.scrollbar.getState();

    expect(view.scrollable).toBe(true);
    expect(view.size).toBeLessThan(1);
    expect(view.offset).toBe(1);
    // The reason the geometry is a second store: it moves here while `pinned`
    // and `unread` do not, so a field on `state` would re-render the whole
    // screen chrome once per inbound frame.
    expect(model.state.getState().pinned).toBe(true);
  });

  it('moves the thumb when the slider seeks', async () => {
    const { model } = await withScrollback();

    model.scrollToOffset(0);

    expect(model.scrollbar.getState().offset).toBe(0);
    expect(model.state.getState().pinned).toBe(false);
  });

  it('counts no unread rows for the seek itself', async () => {
    // A seek moves `viewportY` down exactly as a ring-full trim does, so
    // without the re-baseline the slider would light its own badge.
    const { model } = await withScrollback();

    model.scrollToOffset(0);

    expect(model.state.getState().unread).toBe(0);
  });

  it('keeps counting rows that arrive while the slider is held off the tail', async () => {
    const { model, deliverFrame } = await withScrollback();
    model.scrollToOffset(0);

    deliverFrame(outputFrame(lines(5)));
    await settle();

    expect(model.state.getState().unread).toBeGreaterThan(0);
    expect(model.state.getState().pinned).toBe(false);
  });

  it('returns to the tail when the slider is dragged back down', async () => {
    const { model } = await withScrollback();
    model.scrollToOffset(0);

    model.scrollToOffset(1);

    expect(model.scrollbar.getState().offset).toBe(1);
    expect(model.state.getState().pinned).toBe(true);
    expect(model.state.getState().unread).toBe(0);
  });

  it('retracts the control on the alternate screen, which has no scrollback', async () => {
    const { model, deliverFrame } = await withScrollback();
    expect(model.scrollbar.getState().scrollable).toBe(true);

    deliverFrame(outputFrame(ALT_SCREEN_ON));
    await settle();

    expect(model.scrollbar.getState().scrollable).toBe(false);
  });

  /** jsdom lays nothing out, so the grid height the conversion divides by is stated. */
  function statedHostHeight(model: Model, height: number): void {
    Object.defineProperty(model.host, 'clientHeight', {
      value: height,
      configurable: true,
    });
  }

  it('spends a pixel wheel at one row per cell height', async () => {
    const { model } = await withScrollback();
    // 24 rows over 360 px, so a row is 15 px and 150 px is ten of them.
    statedHostHeight(model, 24 * 15);
    model.scrollToOffset(0);

    model.scrollByPixels(150);

    expect(model.term.buffer.active.viewportY).toBe(10);
  });

  it('carries a sub-row remainder between wheel events', async () => {
    // 70 px is 4 rows and two thirds. Rounding each event down would spend 8
    // rows over two of them; carrying spends 9, which is what the same travel
    // buys over the grid.
    const { model } = await withScrollback();
    statedHostHeight(model, 24 * 15);
    model.scrollToOffset(0);

    model.scrollByPixels(70);
    model.scrollByPixels(70);

    expect(model.term.buffer.active.viewportY).toBe(9);
  });

  it('refuses a pixel wheel while the host has no layout to measure', async () => {
    // Before attach, where jsdom leaves every terminal in these suites.
    const { model } = await withScrollback();
    model.scrollToOffset(0);

    model.scrollByPixels(150);

    expect(model.term.buffer.active.viewportY).toBe(0);
  });

  it('restores it when the program leaves the alternate screen', async () => {
    const { model, deliverFrame } = await withScrollback();
    deliverFrame(outputFrame(ALT_SCREEN_ON));
    await settle();

    deliverFrame(outputFrame(ALT_SCREEN_OFF));
    await settle();

    expect(model.scrollbar.getState().scrollable).toBe(true);
  });
});

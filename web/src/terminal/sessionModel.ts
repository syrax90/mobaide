/**
 * One attached terminal session: the socket, the emulator and every piece of
 * state the screen renders. The web port of Android `TerminalViewModel` and iOS
 * `TerminalSessionModel`.
 *
 * It lives outside React (in `terminalStore`) so the emulator and its socket
 * survive view teardown — navigating away and back keeps the buffer instead of
 * paying for a fresh 4 MiB replay. React reads it through the per-model Zustand
 * store on {@link TerminalSessionModel.state}.
 *
 * The `Terminal` is `open()`ed exactly once, into a host `div` the model owns.
 * A view mounts by adopting that div and unmounts by removing it: xterm cannot
 * `open()` under `display: none` and `FitAddon` mis-measures there, so moving
 * the element is the only shape that survives remounting. Exactly one Terminal
 * is ever in the DOM.
 */
import { Terminal } from '@xterm/xterm';
// Imported here rather than in `main.tsx` so it rides the terminal route's lazy
// chunk: the sign-in path has no terminal and should not download one.
import '@xterm/xterm/css/xterm.css';
import { FitAddon } from '@xterm/addon-fit';
import { ClipboardAddon } from '@xterm/addon-clipboard';
import { Unicode11Addon } from '@xterm/addon-unicode11';
import { create, type StoreApi, type UseBoundStore } from 'zustand';
import {
  terminalTextFrame,
  decodeTerminalFrame,
  type TerminalInboundFrame,
} from '../protocol/terminalCodec';
import {
  decodeTerminalState,
  isSessionRunning,
  type TerminalState,
} from '../protocol/dto/terminalState';
import { writeClipboardText } from '../lib/clipboard';
import { SessionIoChannel, sessionChannelName } from '../realtime/channels';
import { useConnectionStore } from '../realtime/connectionStore';
import { registerDisposable, unregisterDisposable } from '../realtime/liveChannels';
import { realScheduler, type Scheduler } from '../realtime/webSocketChannel';
import { useToastStore } from '../stores/toastStore';
import { useTerminalPrefsStore } from '../stores/terminalPrefsStore';
import { refuseOsc52Reads, TerminalClipboardProvider } from './terminalClipboard';
import {
  foldSample,
  initialFollowState,
  type FollowState,
  type ScrollSample,
} from './terminalFollow';
import { detectIsMac, resolveKeyAction } from './terminalKeys';
import { encodePaste, type PayloadOutcome } from './terminalPaste';
import {
  CLEAR_BEFORE_REPLAY,
  initialReplayState,
  reduceDisconnected,
  reduceInboundFrame,
  type ReplayState,
  type TerminalConnectionState,
} from './terminalReplay';
import {
  attachRenderer,
  type RendererHandle,
  type RendererKind,
} from './terminalRenderer';
import { executePayload, submitPayload } from './terminalSubmit';
import { terminalTheme } from './terminalTheme';

/** Matches Android's and iOS's 100 ms resize debounce. */
const RESIZE_DEBOUNCE_MS = 100;

/**
 * Rows kept above the viewport. The requirement bar is Termux's 10,000; the
 * spike confirmed xterm holds exactly that plus the visible rows.
 */
const SCROLLBACK_ROWS = 10_000;

/** xterm's viewport-paging chords — the only keyboard route out of the tail. */
const VIEWPORT_PAGING_KEYS = ['PageUp', 'PageDown', 'Home', 'End'];

export interface TerminalViewState {
  connection: TerminalConnectionState;
  /** The socket's close policy gave up; nothing will reconnect on its own. */
  channelStopped: boolean;
  /** Replay bytes are queued but not painted yet — keeps the overlay up. */
  replayPainting: boolean;
  status: TerminalState | null;
  pinned: boolean;
  unread: number;
  hasSelection: boolean;
  composer: string;
  composerVisible: boolean;
  renderer: RendererKind;
}

function initialViewState(): TerminalViewState {
  return {
    connection: 'connecting',
    channelStopped: false,
    replayPainting: false,
    status: null,
    pinned: true,
    unread: 0,
    hasSelection: false,
    composer: '',
    composerVisible: false,
    renderer: 'dom',
  };
}

/**
 * Write only what actually changed.
 *
 * Zustand notifies on every `setState`, and the hot paths here run per inbound
 * frame — `handleInbound` republishes the connection state on every 64 KiB
 * output chunk. Without the diff a busy session would re-render the whole
 * screen chrome at the rate the PTY produces bytes. Same conflation
 * `connectionStore.sameState` does for the channel map.
 */
function patchState(
  store: UseBoundStore<StoreApi<TerminalViewState>>,
  patch: Partial<TerminalViewState>,
): void {
  const current = store.getState();
  for (const [key, value] of Object.entries(patch)) {
    if (current[key as keyof TerminalViewState] !== value) {
      store.setState(patch);
      return;
    }
  }
}

export interface TerminalSessionModelOptions {
  scheduler?: Scheduler;
  /** Injected in tests so the model runs without a real socket. */
  channel?: SessionIoChannel;
  /**
   * Run last during {@link TerminalSessionModel.dispose}.
   *
   * A callback rather than a `terminalStore` import: the store already imports
   * this module, and `stopAllRealtime()` disposes models behind the store's
   * back, so the entry has to be dropped from here. Same cycle-avoidance the
   * realtime layer uses for `liveChannels`.
   */
  onDispose?: () => void;
}

export class TerminalSessionModel {
  readonly key: string;
  readonly agentId: string;
  readonly sessionId: string;
  readonly term: Terminal;
  readonly host: HTMLDivElement;
  readonly state: UseBoundStore<StoreApi<TerminalViewState>>;

  private readonly io: SessionIoChannel;
  private readonly fit = new FitAddon();
  private readonly scheduler: Scheduler;
  private readonly onDispose: (() => void) | undefined;
  private readonly disposers: (() => void)[] = [];

  private renderer: RendererHandle | null = null;
  private resizeObserver: ResizeObserver | null = null;
  private opened = false;
  private disposed = false;

  private replay: ReplayState = initialReplayState();
  private follow: FollowState = initialFollowState();
  /** Replay writes handed to the parser but not yet acknowledged. */
  private pendingReplayWrites = 0;
  private replayEndSeen = false;
  /** Consumed by the next sample: re-baseline instead of accruing unread. */
  private rebaseline = false;
  private pointerDragging = false;

  private lastRows = 0;
  private lastCols = 0;
  private cancelResize: (() => void) | null = null;

  constructor(
    agentId: string,
    sessionId: string,
    options: TerminalSessionModelOptions = {},
  ) {
    this.agentId = agentId;
    this.sessionId = sessionId;
    this.key = sessionChannelName(agentId, sessionId);
    this.scheduler = options.scheduler ?? realScheduler;
    this.onDispose = options.onDispose;
    this.state = create<TerminalViewState>()(() => initialViewState());

    this.host = document.createElement('div');
    this.host.className = 'terminal-host';

    this.term = new Terminal({
      allowProposedApi: true,
      scrollback: SCROLLBACK_ROWS,
      // Starts closed and is kept in step with the send gate by
      // `syncStdinGate()`; nothing may reach the PTY before the first live entry.
      disableStdin: true,
      // The PTY's termios owns the LF -> CRLF translation; doing it here too
      // would double-space everything a real shell prints.
      convertEol: false,
      cursorBlink: true,
      fontFamily:
        'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace',
      fontSize: 13,
      // Left at xterm's default `true`: it scrolls to the tail on *genuine*
      // user input only, which is what keeps a program's own `ESC[6n` reply
      // from un-pausing a scrolled-up viewport. Both mobile clients had to
      // build that discrimination by hand (Android CLA-184, iOS CLA-178).
      scrollOnUserInput: true,
      theme: terminalTheme(),
    });

    this.term.loadAddon(this.fit);
    this.term.loadAddon(new Unicode11Addon());
    this.term.unicode.activeVersion = '11';
    this.term.loadAddon(new ClipboardAddon(undefined, new TerminalClipboardProvider()));
    // Registered after the addon on purpose: xterm dispatches OSC handlers
    // last-registered-first, so this one sees the query first and swallows it.
    this.track(refuseOsc52Reads(this.term));

    this.io = options.channel ?? new SessionIoChannel(agentId, sessionId);
    this.wireTerminal();
    this.wireChannel();

    registerDisposable(this);
    this.io.channel.connect();
  }

  /**
   * Adopt the model's host element into a laid-out container.
   *
   * `open()` is deferred until the container actually has a size: xterm takes
   * DOM measurements inside `open`, and a zero-height parent produces a grid it
   * never recovers from.
   */
  attach(container: HTMLElement): void {
    if (this.disposed) return;
    this.detach();
    container.appendChild(this.host);
    const observer = new ResizeObserver(() => {
      this.ensureOpen();
      this.fitNow();
    });
    observer.observe(container);
    this.resizeObserver = observer;
    this.ensureOpen();
    this.fitNow();
    // Re-assert unconditionally, bypassing `pushSize`'s dedupe. A retained
    // model keeps its socket open across detach/reattach, so none of the other
    // three re-assert points (socket open, first binary while reconnecting,
    // `replayEnd`) fires on a revisit — and if the grid is unchanged locally
    // the dedupe swallows the only remaining one. A session another client
    // resized would then keep that client's grid while this one renders its
    // own. One extra `r` frame per attach is the whole cost.
    this.assertSize();
  }

  detach(): void {
    this.resizeObserver?.disconnect();
    this.resizeObserver = null;
    this.host.remove();
  }

  focus(): void {
    this.term.focus();
  }

  private ensureOpen(): void {
    if (this.opened || this.disposed) return;
    if (this.host.clientWidth === 0 || this.host.clientHeight === 0) return;
    this.term.open(this.host);
    this.opened = true;
    this.renderer = attachRenderer(this.term, {
      scheduler: this.scheduler,
      onChange: (renderer) => {
        patchState(this.state, { renderer });
      },
    });
    this.installDomListeners();
  }

  private fitNow(): void {
    if (!this.opened) return;
    try {
      this.fit.fit();
    } catch {
      // `proposeDimensions` reads the renderer's cell metrics, which are absent
      // for a frame after `open()` in some browsers. The next observer tick
      // measures again.
      return;
    }
    // Seed the cache from the *resolved* grid rather than relying on
    // `onResize`. `fit()` only resizes when the proposal differs from the
    // current grid, and a viewport that happens to fit xterm's 80x24 default
    // therefore fires no event at all — leaving the cache at zero and turning
    // all three re-assert points into permanent no-ops. On a reattach to a
    // session another client already resized, that would leave the PTY on the
    // other client's grid. `pushSize` dedupes, so calling it every tick is free.
    this.pushSize(this.term.rows, this.term.cols);
  }

  private wireTerminal(): void {
    this.track(this.term.onData((data) => {
      this.sendInputText(data);
    }));
    // Mouse reports that are not valid UTF-8 arrive here instead of `onData`;
    // dropping them would silently break X10 and SGR mouse tracking.
    this.track(
      this.term.onBinary((data) => {
        this.sendInputBytes(Uint8Array.from(data, (ch) => ch.charCodeAt(0) & 0xff));
      }),
    );
    this.track(this.term.onWriteParsed(() => {
      this.sampleFollow();
    }));
    this.track(this.term.onScroll(() => {
      this.sampleFollow();
    }));
    this.track(
      this.term.onResize(({ rows, cols }) => {
        // A reflow moves both buffer indices, so the tally has to re-baseline
        // rather than read the shift as output the user missed.
        this.rebaseline = true;
        this.sampleFollow();
        this.pushSize(rows, cols);
      }),
    );
    this.track(
      this.term.buffer.onBufferChange(() => {
        // The alternate screen renumbers everything and has no scrollback; a
        // selection taken on the primary buffer no longer names its own text.
        this.term.clearSelection();
        this.rebaseline = true;
        this.sampleFollow();
      }),
    );
    this.track(
      this.term.onSelectionChange(() => {
        patchState(this.state, { hasSelection: this.term.hasSelection() });
      }),
    );

    this.term.attachCustomWheelEventHandler(() => {
      this.rebaseline = true;
      return true;
    });
    this.term.attachCustomKeyEventHandler((event) => this.handleKeyEvent(event));
  }

  private installDomListeners(): void {
    const element = this.term.element;
    if (element === undefined) return;

    const onPaste = (event: ClipboardEvent): void => {
      // Capture phase, so this pre-empts xterm's own paste handler — which does
      // the newline rewrite and the bracket wrap but no ESC/C1 strip.
      event.preventDefault();
      event.stopPropagation();
      this.pasteText(event.clipboardData?.getData('text/plain') ?? '');
    };
    const onPointerDown = (): void => {
      this.pointerDragging = true;
      this.rebaseline = true;
    };
    const endDrag = (): void => {
      this.pointerDragging = false;
    };

    element.addEventListener('paste', onPaste, true);
    element.addEventListener('pointerdown', onPointerDown, true);
    // On `window`, not the element: a drag released outside the terminal would
    // otherwise leave the flag stuck and suppress the tally for good.
    window.addEventListener('pointerup', endDrag);
    window.addEventListener('pointercancel', endDrag);

    this.disposers.push(() => {
      element.removeEventListener('paste', onPaste, true);
      element.removeEventListener('pointerdown', onPointerDown, true);
      window.removeEventListener('pointerup', endDrag);
      window.removeEventListener('pointercancel', endDrag);
    });
  }

  private handleKeyEvent(event: KeyboardEvent): boolean {
    if (
      event.type === 'keydown' &&
      event.shiftKey &&
      VIEWPORT_PAGING_KEYS.includes(event.key)
    ) {
      this.rebaseline = true;
    }
    // App-owned chords never reach here: `useAppShortcuts` claims them on a
    // document-level capture listener, because xterm registers its `keydown` on
    // the terminal's textarea and this handler would otherwise fire only while
    // the grid has focus.
    const action = resolveKeyAction(event, {
      hasSelection: this.term.hasSelection(),
      passCtrlVToTerminal: useTerminalPrefsStore.getState().passCtrlVToTerminal,
      isMac: detectIsMac(navigator.platform),
    });
    if (action === 'copy') {
      event.preventDefault();
      void this.copySelection();
      return false;
    }
    if (action === 'browserPaste') {
      // Deliberately no `preventDefault`: the browser's own paste runs and the
      // capture-phase listener above sanitizes it. `clipboard.readText()` would
      // be gated behind a per-read prompt in Firefox and Safari.
      return false;
    }
    return true;
  }

  private track(disposable: { dispose: () => void }): void {
    this.disposers.push(() => {
      disposable.dispose();
    });
  }

  private wireChannel(): void {
    this.disposers.push(
      this.io.channel.subscribe((event) => {
        switch (event.kind) {
          case 'binary':
            this.handleInbound(decodeTerminalFrame(new Uint8Array(event.data)));
            break;
          case 'disconnected':
            this.replay = reduceDisconnected();
            patchState(this.state, { connection: this.replay.connection });
            this.syncStdinGate();
            break;
          default:
            break;
        }
      }),
    );
    this.disposers.push(
      useConnectionStore.subscribe((store) => {
        patchState(this.state, {
          channelStopped: store.channels[this.key]?.status === 'stopped',
        });
      }),
    );
  }

  private handleInbound(frame: TerminalInboundFrame | null): void {
    const { state, effects } = reduceInboundFrame(this.replay, frame);
    this.replay = state;

    for (const effect of effects) {
      switch (effect.kind) {
        case 'wipe':
          // A selection taken before the wipe names rows that are about to be
          // renumbered; iOS learned this the expensive way.
          this.term.clearSelection();
          this.rebaseline = true;
          // Each span owns its own end marker. A socket that dropped after `'e'`
          // but before the writes drained would otherwise leave the flag raised,
          // and a callback from the *old* span would clear the new span's
          // overlay over a screen the parser has not painted.
          this.replayEndSeen = false;
          patchState(this.state, { replayPainting: true });
          this.writeReplay(CLEAR_BEFORE_REPLAY);
          break;
        case 'write':
          if (this.replay.inReplay) this.writeReplay(effect.payload);
          else this.writeLive(effect.payload);
          break;
        case 'state':
          this.state.setState({ status: decodeTerminalState(effect.json) });
          break;
        case 'replayFinished':
          this.replayEndSeen = true;
          this.settleReplayPaint();
          break;
        case 'enterLive':
          this.assertSize();
          break;
      }
    }
    patchState(this.state, { connection: this.replay.connection });
    this.syncStdinGate();
  }

  /**
   * Write live output.
   *
   * `WriteBuffer.write` throws once its 50 MB backlog watermark is crossed,
   * which a tab backgrounded long enough for Chrome to throttle the parse loop
   * can reach while the socket keeps delivering. Unguarded, that throw escapes
   * through the channel's `emit` into `socket.onmessage` as an uncaught error.
   */
  private writeLive(payload: Uint8Array): void {
    try {
      this.term.write(payload);
    } catch {
      // Discarded by xterm's own flow control. The next replay restores the
      // screen; there is nothing useful to do with a chunk the emulator refused.
    }
  }

  /**
   * Write a replay chunk and count it, so the overlay can stay up until the
   * bytes are actually parsed. `write` callbacks fire post-parse and in order,
   * which is exactly the bookkeeping this needs.
   *
   * A discard throw is undone rather than left pending: `WriteBuffer.write`
   * raises before queueing, so no callback will ever arrive to balance the
   * increment, and a stranded counter now pins the send gate as well as the
   * overlay. Incrementing first also keeps the count non-negative when xterm
   * parses synchronously (it does, on the first write after user input).
   */
  private writeReplay(payload: Uint8Array): void {
    this.pendingReplayWrites += 1;
    try {
      this.term.write(payload, () => {
        this.pendingReplayWrites -= 1;
        this.settleReplayPaint();
        // The counter is a term in the send gate, so the drain that releases it
        // has to re-open stdin.
        this.syncStdinGate();
      });
    } catch {
      this.pendingReplayWrites -= 1;
      this.settleReplayPaint();
      this.syncStdinGate();
    }
  }

  private settleReplayPaint(): void {
    if (!this.replayEndSeen || this.pendingReplayWrites > 0) return;
    this.replayEndSeen = false;
    patchState(this.state, { replayPainting: false });
  }

  /**
   * Cache the measured grid and schedule the `r` frame.
   *
   * The cache is written **before** the debounce, exactly as both mobile
   * clients do: a layout pass that settles before the socket opens must still
   * record the size so {@link assertSize} can deliver it on connect. Without
   * that the PTY stays at the 24x80 it was spawned with and a full-screen TUI
   * paints into the top 24 rows.
   */
  private pushSize(rows: number, cols: number): void {
    if (rows === this.lastRows && cols === this.lastCols) return;
    this.lastRows = rows;
    this.lastCols = cols;
    this.cancelResize?.();
    this.cancelResize = this.scheduler.schedule(() => {
      this.cancelResize = null;
      this.assertSize();
    }, RESIZE_DEBOUNCE_MS);
  }

  /** Re-deliver the cached grid. The client is the sole size authority. */
  private assertSize(): void {
    if (this.lastRows <= 0 || this.lastCols <= 0) return;
    this.io.setSize(this.lastRows, this.lastCols);
  }

  private currentSample(): ScrollSample {
    const buffer = this.term.buffer.active;
    return { baseY: buffer.baseY, viewportY: buffer.viewportY };
  }

  private sampleFollow(): void {
    const userDriven = this.rebaseline || this.pointerDragging;
    this.rebaseline = false;
    const previous = this.follow;
    const next = foldSample(previous, this.currentSample(), userDriven);
    this.follow = next;
    if (next.pinned === previous.pinned && next.unread === previous.unread) return;
    this.state.setState({ pinned: next.pinned, unread: next.unread });
  }

  jumpToBottom(): void {
    this.rebaseline = true;
    this.term.scrollToBottom();
    this.sampleFollow();
  }

  /**
   * Re-arm a channel that gave up.
   *
   * `WebSocketChannel` stops for good after a terminal close code, five
   * unbuildable URLs (an agent-token mint failing through a network outage
   * reaches that in seconds), or a throwing `buildUrl` — and nothing re-arms it
   * after the constructor, so without this the screen is bricked until the tab
   * reloads or the model is evicted. `connect()` sets `shouldReconnect` again
   * and is idempotent on a healthy channel.
   */
  retryConnection(): void {
    if (this.disposed) return;
    this.io.channel.connect();
  }

  /**
   * Whether the emulator's output may reach the PTY.
   *
   * The `pendingReplayWrites` term is the web-specific half. `'e'` flips the
   * connection to live synchronously, but `term.write` is asynchronous, so on a
   * multi-MiB ring the parser is still chewing through replayed history long
   * afterwards. Any query sequence in that history — DECRQM for mode 2026,
   * XTVERSION, DECRQSS, OSC 4, none of which the agent's ring scrub removes —
   * would be answered *after* the gate opened and injected into the user's live
   * prompt. Android and iOS never see this because their replay append finishes
   * before their gate flips. Outside a replay span the counter is zero, so live
   * auto-replies are unaffected.
   */
  private canSendBytes(): boolean {
    return (
      this.replay.connection === 'live' &&
      this.pendingReplayWrites === 0 &&
      isSessionRunning(this.state.getState().status)
    );
  }

  /**
   * Mirror the send gate into xterm, so a key that cannot reach the PTY has no
   * side effects either.
   *
   * `disableStdin` short-circuits `triggerDataEvent`/`triggerBinaryEvent`
   * *before* two things xterm does on user input: scrolling the viewport back
   * to the tail (which silently clears the unread badge) and clearing the
   * selection. Without it, typing into a stopped session — exactly when the
   * terminal is a read-only transcript someone is scrolling back through and
   * selecting to copy — throws away their scroll position and their selection
   * while the keystroke goes nowhere.
   *
   * Deliberately not done in `attachCustomKeyEventHandler`: returning `false`
   * there skips xterm's `preventDefault`, so Tab would escape the terminal,
   * Space would scroll the page and `/` would open Firefox's quick-find. This
   * gate leaves the viewport-paging chords and the clipboard chords working,
   * because those never produce data in the first place.
   */
  private syncStdinGate(): void {
    const disabled = !this.canSendBytes();
    if (this.term.options.disableStdin !== disabled) {
      this.term.options.disableStdin = disabled;
    }
  }

  private sendInputText(text: string): void {
    if (text.length === 0 || !this.canSendBytes()) return;
    this.io.send(terminalTextFrame('input', text));
  }

  private sendInputBytes(payload: Uint8Array): void {
    if (payload.length === 0 || !this.canSendBytes()) return;
    this.io.send({ kind: 'input', payload });
  }

  /**
   * Send an encoded payload, or explain why it was refused.
   *
   * The one place the 1 MiB ceiling is enforced for every outbound text frame.
   * It is an availability control: the relay forwards a client frame to the
   * agent's tunnel unchunked, and the agent opens that tunnel with
   * `max_size=4 MB`, so one oversized frame closes it with 1009 and drops every
   * session on that agent — not just this one.
   */
  private sendPayload(kind: 'input' | 'submit' | 'execute', outcome: PayloadOutcome): boolean {
    if (outcome.kind === 'empty') return false;
    if (outcome.kind === 'tooLarge') {
      useToastStore
        .getState()
        .show('That is over the 1 MB limit for one message and was not sent.', 'error');
      return false;
    }
    return this.io.send(terminalTextFrame(kind, outcome.text));
  }

  pasteText(text: string): void {
    if (!this.canSendBytes()) return;
    // One frame, not Android's three: these cross a WebSocket and a relay, and
    // losing only the closing marker would strand the program's line editor in
    // paste-collect mode and read as a dead keyboard.
    this.sendPayload('input', encodePaste(text, this.term.modes.bracketedPasteMode));
  }

  /**
   * Send a control-bar button or a saved chord as raw bytes.
   *
   * Jumps to the tail for the same reason typing does: xterm scrolls to the
   * bottom on genuine user input, and a button press is user input. Android
   * `TerminalViewModel.sendBytes` and iOS `sendControlKey` both do it too.
   */
  sendChordBytes(payload: Uint8Array): void {
    if (payload.length === 0 || !this.canSendBytes()) return;
    this.sendInputBytes(payload);
    this.jumpToBottom();
  }

  setComposer(text: string): void {
    this.state.setState({ composer: text });
  }

  setComposerVisible(visible: boolean): void {
    this.state.setState({ composerVisible: visible });
  }

  submitComposer(): void {
    if (!this.canSendBytes()) return;
    if (!this.sendPayload('submit', submitPayload(this.state.getState().composer))) {
      return;
    }
    this.state.setState({ composer: '' });
    this.jumpToBottom();
  }

  /**
   * Whether the painted screen is trustworthy enough to act on a selection.
   *
   * Checked at the instant of the action rather than cached in the view state:
   * xterm clears mode 2026 on a 1 s safety timeout inside `RenderService` with
   * **no** event of any kind, so a flag sampled on writes or selection changes
   * has a transition it can never observe and would strand the toolbar disabled
   * on a screen that is actually current. Reading it here also means a button
   * cannot go `disabled` between pointerdown and mouseup and swallow the click.
   *
   * Both mobile clients refuse the same reads for the same reason: while output
   * is synchronized the buffer is ahead of what the user can see, so a copy
   * would hand back text that was never on screen.
   */
  private screenIsSettled(): boolean {
    // Two ways the buffer can be ahead of what the user can see: the program
    // armed mode 2026, or the parser is still reconstructing replayed history.
    // Copy is the one that matters most — it would put text the user never saw
    // onto the system clipboard.
    if (!this.term.modes.synchronizedOutputMode && this.pendingReplayWrites === 0) {
      return true;
    }
    useToastStore
      .getState()
      .show('The screen is still painting — try that again in a moment.', 'info');
    return false;
  }

  /**
   * Run the current selection.
   *
   * Gated on `live` only, not on `running` — the mobile row tap this stands in
   * for is reachable on a stopped session too, which is how a crashed command
   * gets retried.
   */
  runSelection(): void {
    if (this.replay.connection !== 'live' || !this.screenIsSettled()) return;
    if (!this.sendPayload('execute', executePayload(this.term.getSelection()))) {
      return;
    }
    this.term.clearSelection();
    this.jumpToBottom();
    // Clearing the selection unmounts the toolbar, taking the focused button
    // with it — focus would land on `document.body` and the keyboard would stop
    // reaching the PTY until the user clicked the grid again. `Terminal.focus()`
    // uses `preventScroll`, so it cannot yank a scrolled-up viewport.
    this.focus();
  }

  async copySelection(): Promise<void> {
    if (!this.screenIsSettled()) return;
    const text = this.term.getSelection();
    if (text === '') return;
    const written = await writeClipboardText(text);
    // The model can be disposed while the clipboard promise is in flight —
    // `stopAllRealtime()` fires on every session change — and the continuation
    // would then toast for, and reach into, a dead terminal.
    if (this.disposed) return;
    if (!written) {
      useToastStore.getState().show('The browser refused the clipboard write.', 'error');
      return;
    }
    this.term.clearSelection();
    // Same reason as `runSelection`: the toolbar button that was clicked is
    // about to leave the DOM. A no-op when the copy came from Ctrl+C.
    this.focus();
  }

  sendSelectionToComposer(): void {
    if (!this.screenIsSettled()) return;
    const text = this.term.getSelection();
    if (text === '') return;
    this.state.setState({ composer: text, composerVisible: true });
    this.term.clearSelection();
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    unregisterDisposable(this);
    this.cancelResize?.();
    this.cancelResize = null;
    this.detach();
    for (const dispose of this.disposers.splice(0)) dispose();
    this.renderer?.dispose();
    this.renderer = null;
    this.io.dispose();
    this.term.dispose();
    this.onDispose?.();
  }
}

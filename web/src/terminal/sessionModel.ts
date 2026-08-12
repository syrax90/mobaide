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
import { onTabVisibility, tabHidden } from '../realtime/tabVisibility';
import { realScheduler, type Scheduler } from '../realtime/webSocketChannel';
import { useToastStore } from '../stores/toastStore';
import { shouldSuspend, type BackgroundSpend } from './terminalSuspend';
import { mountedTerminal, onMountedTerminal } from './watchedTerminal';
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
import {
  initialScrollbarView,
  lineForOffset,
  relativeSteps,
  sameScrollbarView,
  scrollbarView,
  wheelRows,
  type ScrollbarView,
} from './terminalScrollbar';
import { executePayload } from './terminalSubmit';
import { terminalTheme } from './terminalTheme';

/** Matches Android's and iOS's 100 ms resize debounce. */
const RESIZE_DEBOUNCE_MS = 100;

/** The one font size the terminal renders at while this client owns the grid. */
const NATIVE_FONT_SIZE = 13;

/**
 * Floor for the follower-mode font solve. Below this the text is unreadable
 * anyway; a foreign grid that still does not fit is clamped and clipped.
 */
const MIN_FOLLOWER_FONT_SIZE = 6;

/**
 * How long window focus must hold before a passive client takes size
 * authority (proto-v8 latest-active arbitration). Matches Android/iOS's
 * foreground-takeover debounce. Typing takes over immediately instead.
 */
const FOCUS_TAKEOVER_MS = 400;

/**
 * The agent's per-dimension grid envelope (`MAX_GRID_DIM` in
 * `mobaide/core/session.py`). A grid outside it is rejected server-side with
 * no corrective `w`, so the emulator must never adopt one: a wide viewport at
 * low browser zoom can propose more than 1000 columns, and sizing the
 * emulator to it would leave local wrapping permanently disagreeing with the
 * PTY. The browser is the only client that can reach this — both mobile
 * viewports are physically orders of magnitude below the envelope.
 */
const MAX_GRID_DIM = 1000;

/**
 * This client's role in proto-v8 size arbitration. `solo` and `owner` behave
 * exactly like the pre-v8 sole-authority client; `follower` renders another
 * client's grid and never sends `r` frames. Fresh models start as `follower`
 * so an attach to a session another client is driving *adopts* the broadcast
 * size instead of stealing it — the handshake's `w` frame (or replay-end on a
 * v7 agent that sends none) resolves the real role.
 */
export type SizeAuthority = 'solo' | 'owner' | 'follower';

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
  /** Detached on purpose to stop spending bandwidth on an unwatched session. */
  suspended: boolean;
  /** Replay bytes are queued but not painted yet — keeps the overlay up. */
  replayPainting: boolean;
  status: TerminalState | null;
  pinned: boolean;
  unread: number;
  hasSelection: boolean;
  renderer: RendererKind;
  /** Size-arbitration role; `follower` means the grid belongs to another client. */
  sizeAuthority: SizeAuthority;
  /** Attached-client count from the last `w` frame; 0 before any arrived. */
  attachedClients: number;
}

function initialViewState(): TerminalViewState {
  return {
    connection: 'connecting',
    channelStopped: false,
    suspended: false,
    replayPainting: false,
    status: null,
    pinned: true,
    unread: 0,
    hasSelection: false,
    renderer: 'dom',
    sizeAuthority: 'follower',
    attachedClients: 0,
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
  /** Injected so a test can drive the suspend grace without a fake clock. */
  now?: () => number;
  /**
   * The suspend policy, injected so a test can exercise this wiring without
   * pushing `RECOUP_BYTES` through the emulator. `terminalSuspend.test` owns
   * the thresholds themselves.
   */
  suspendPolicy?: (spend: BackgroundSpend) => boolean;
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
  /**
   * Slider geometry, deliberately kept out of {@link state}.
   *
   * `TerminalView` subscribes to the whole view store, so a field there would
   * re-render the entire screen chrome — the header bar and every overlay —
   * every time the thumb moved, which is every row a busy PTY prints. Only
   * `TerminalScrollbar` reads this one, and `publishScrollbar` quantizes before
   * writing it.
   */
  readonly scrollbar: UseBoundStore<StoreApi<ScrollbarView>>;

  private readonly io: SessionIoChannel;
  private readonly fit = new FitAddon();
  private readonly scheduler: Scheduler;
  private readonly now: () => number;
  private readonly suspendPolicy: (spend: BackgroundSpend) => boolean;
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
  /** Sub-row wheel remainder, so a touchpad's small deltas are not rounded off. */
  private wheelCarry = 0;

  private lastRows = 0;
  private lastCols = 0;
  /**
   * The grid actually put on the wire last, which can trail {@link lastRows}/
   * {@link lastCols} by a debounce while the user is still resizing. The
   * own-echo check accepts only this pair — the cache also holds
   * merely-measured grids, which must never read as a claim.
   */
  private lastSentRows = 0;
  private lastSentCols = 0;
  private cancelResize: (() => void) | null = null;

  /**
   * Size-arbitration role (see {@link SizeAuthority}). Source of truth; the
   * view-state copy exists only for the "following" badge.
   */
  private authority: SizeAuthority = 'follower';
  private followerGrid: { rows: number; cols: number } | null = null;
  /**
   * Whether a `w` frame arrived on the current socket. Reset on disconnect;
   * a replay-end with no `w` seen means a v7 agent, and the pre-v8 solo
   * behavior applies.
   */
  private winsizeSeen = false;
  /**
   * Whether a state frame arrived on the current socket — the earliest this
   * socket's own handshake can have started. `w` frames before it are relay
   * broadcasts that predate this client's registration and must not be
   * arbitrated on. Reset on disconnect.
   */
  private stateSeen = false;
  /** Dedupe key of the last follower solve: grid + container size. */
  private lastFollowerSolve: string | null = null;
  private cancelFocusTakeover: (() => void) | null = null;
  private removeFocusListeners: (() => void) | null = null;
  /** An elapsed focus claim waiting for the send gate; see tryFocusTakeover. */
  private pendingFocusTakeover = false;

  /** When the session stopped being read, or null while it is being read. */
  private unwatchedSince: number | null = null;
  /** Live output charged since then. Replay spans are not charged. */
  private unwatchedBytes = 0;
  private suspended = false;

  constructor(
    agentId: string,
    sessionId: string,
    options: TerminalSessionModelOptions = {},
  ) {
    this.agentId = agentId;
    this.sessionId = sessionId;
    this.key = sessionChannelName(agentId, sessionId);
    this.scheduler = options.scheduler ?? realScheduler;
    this.now = options.now ?? (() => Date.now());
    this.suspendPolicy = options.suspendPolicy ?? shouldSuspend;
    this.onDispose = options.onDispose;
    this.state = create<TerminalViewState>()(() => initialViewState());
    this.scrollbar = create<ScrollbarView>()(() => initialScrollbarView());

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
      fontSize: NATIVE_FONT_SIZE,
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
    // Fresh models start as followers, so the socket-open re-assert stays
    // muted until the handshake resolves the real role.
    this.io.setResendSuppressed(true);
    this.wireTerminal();
    this.wireChannel();
    this.wireWatchGate();

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
    // Re-assert unconditionally, bypassing `pushSize`'s dedupe (but not the
    // follower gate inside `assertSize`). A retained model keeps its socket
    // open across detach/reattach, so none of the other three re-assert
    // points (socket open, first binary while reconnecting, `replayEnd`)
    // fires on a revisit — and if the grid is unchanged locally the dedupe
    // swallows the only remaining one. A session another client resized
    // would then keep that client's grid while this one renders its own.
    // One extra `r` frame per attach is the whole cost.
    this.assertSize();
    this.installFocusListeners();
    // Opening the screen in a focused tab is itself a focus edge: the user
    // is looking at this terminal, so a follower arms the takeover the same
    // way mobile's screen-visible signal does. A background-tab reconnect
    // never passes this check and stays passive.
    if (document.hasFocus() && !document.hidden) this.armFocusTakeover();
  }

  detach(): void {
    this.resizeObserver?.disconnect();
    this.resizeObserver = null;
    this.removeFocusListeners?.();
    this.removeFocusListeners = null;
    this.cancelFocusTakeover?.();
    this.cancelFocusTakeover = null;
    this.pendingFocusTakeover = false;
    this.host.remove();
  }

  /**
   * Focus edges arm the follower takeover; loss of focus cancels it.
   *
   * Edges only, never steady state: re-arming on demotion would have a
   * focused-but-idle tab steal authority back 400 ms after every foreign
   * resize, ping-ponging against the client the user is actually typing on.
   */
  private installFocusListeners(): void {
    const onFocus = (): void => {
      this.armFocusTakeover();
    };
    const onBlur = (): void => {
      this.cancelFocusTakeover?.();
      this.cancelFocusTakeover = null;
      this.pendingFocusTakeover = false;
    };
    const onVisibility = (): void => {
      if (document.hidden) onBlur();
      else if (document.hasFocus()) this.armFocusTakeover();
    };
    window.addEventListener('focus', onFocus);
    window.addEventListener('blur', onBlur);
    document.addEventListener('visibilitychange', onVisibility);
    this.removeFocusListeners = () => {
      window.removeEventListener('focus', onFocus);
      window.removeEventListener('blur', onBlur);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }

  private armFocusTakeover(): void {
    if (this.authority !== 'follower') return;
    this.cancelFocusTakeover?.();
    this.cancelFocusTakeover = this.scheduler.schedule(() => {
      this.cancelFocusTakeover = null;
      this.tryFocusTakeover();
    }, FOCUS_TAKEOVER_MS);
  }

  /**
   * An elapsed focus claim that cannot send yet (reconnecting, or the replay
   * is still draining) is kept pending instead of dropped — steady focus
   * produces no new edge to re-arm it, so a claim discarded here would leave
   * the returning tab stuck as a follower until the user types. The pending
   * claim is retried as the send gate opens and dies on blur/hide/detach.
   */
  private tryFocusTakeover(): void {
    if (this.authority !== 'follower') return;
    if (!this.canSendBytes()) {
      this.pendingFocusTakeover = true;
      return;
    }
    this.takeoverNow();
  }

  private maybeRetryFocusTakeover(): void {
    if (!this.pendingFocusTakeover) return;
    if (this.authority !== 'follower' || document.hidden || !document.hasFocus()) {
      this.pendingFocusTakeover = false;
      return;
    }
    if (!this.canSendBytes()) return;
    this.pendingFocusTakeover = false;
    this.takeoverNow();
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
    if (this.authority === 'follower' && this.followerGrid !== null) {
      // The grid belongs to another client: solve the font for it instead of
      // fitting the container. The observer path lands here too, so a window
      // resize while passive re-solves the font rather than sending `r`.
      this.solveFollowerLayout();
      return;
    }
    try {
      // `fit()` would adopt the proposal unclamped, so an out-of-envelope
      // proposal takes the manual-resize path instead.
      const dims = this.fit.proposeDimensions();
      if (
        dims !== undefined &&
        (dims.rows > MAX_GRID_DIM || dims.cols > MAX_GRID_DIM)
      ) {
        const rows = Math.min(dims.rows, MAX_GRID_DIM);
        const cols = Math.min(dims.cols, MAX_GRID_DIM);
        if (this.term.rows !== rows || this.term.cols !== cols) {
          this.term.resize(cols, rows);
        }
      } else {
        this.fit.fit();
      }
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

  /**
   * Passive rendering: pick the largest font at which the foreign grid fits
   * the container, then resize the emulator to that grid *exactly*.
   *
   * `proposeDimensions()` is the oracle — it subtracts the scrollbar gutter
   * and reads the renderer's real cell metrics after each font change, so no
   * linear-scaling assumption is made. Never `transform: scale`: it breaks
   * xterm's character measurement and selection mapping (xterm #2488/#3242).
   * If even the floor font cannot fit the grid, it is clamped and the host
   * clips — typing takes authority and restores the native layout anyway.
   */
  private solveFollowerLayout(): void {
    if (!this.opened || this.followerGrid === null) return;
    const { rows, cols } = this.followerGrid;
    const key = `${String(rows)}x${String(cols)}@${String(this.host.clientWidth)}x${String(this.host.clientHeight)}`;
    if (
      key === this.lastFollowerSolve &&
      this.term.rows === rows &&
      this.term.cols === cols
    ) {
      return;
    }
    let low = MIN_FOLLOWER_FONT_SIZE;
    let high = NATIVE_FONT_SIZE;
    let chosen: number | null = null;
    while (low <= high) {
      const probe = Math.floor((low + high) / 2);
      const dims = this.proposeAt(probe);
      if (dims === null) {
        // Renderer metrics not ready (same window as `fitNow`'s catch): put
        // the font back and let the next observer tick retry the solve.
        this.term.options.fontSize = NATIVE_FONT_SIZE;
        return;
      }
      if (dims.cols >= cols && dims.rows >= rows) {
        chosen = probe;
        low = probe + 1;
      } else {
        high = probe - 1;
      }
    }
    const fontSize = chosen ?? MIN_FOLLOWER_FONT_SIZE;
    if (this.term.options.fontSize !== fontSize) {
      this.term.options.fontSize = fontSize;
    }
    if (this.term.rows !== rows || this.term.cols !== cols) {
      // Echoes back through `onResize` → `pushSize`, which the follower gate
      // drops before it can touch the cache or send a frame.
      this.term.resize(cols, rows);
    }
    this.lastFollowerSolve = key;
  }

  /** Grid the container would hold at `fontSize`, or null while unmeasurable. */
  private proposeAt(fontSize: number): { rows: number; cols: number } | null {
    if (this.term.options.fontSize !== fontSize) {
      this.term.options.fontSize = fontSize;
    }
    try {
      const dims = this.fit.proposeDimensions();
      if (
        dims === undefined ||
        !Number.isFinite(dims.rows) ||
        !Number.isFinite(dims.cols)
      ) {
        return null;
      }
      return { rows: dims.rows, cols: dims.cols };
    } catch {
      return null;
    }
  }

  private restoreNativeLayout(): void {
    this.lastFollowerSolve = null;
    if (this.term.options.fontSize !== NATIVE_FONT_SIZE) {
      this.term.options.fontSize = NATIVE_FONT_SIZE;
    }
    this.fitNow();
  }

  private wireTerminal(): void {
    // `onKey` fires for genuine keyboard input only — never for the emulator's
    // auto-replies (DA/DSR/DECRQM answers), which also flow through `onData`
    // and must not claim size authority. It fires before the data event, so a
    // takeover's resize frame precedes the keystroke's bytes on the wire.
    this.track(this.term.onKey(() => {
      this.takeoverNow();
    }));
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
            // Charged after the frame is handled, never before. Suspending
            // resets the replay machine to `connecting`, and this same frame
            // would then re-upgrade it to live through `reduceInboundFrame`'s
            // connecting branch — reopening the send gate over a closed socket.
            this.chargeInbound(event.data.byteLength);
            break;
          case 'disconnected':
            this.noteDisconnected();
            break;
          default:
            break;
        }
      }),
    );
    this.disposers.push(
      useConnectionStore.subscribe(() => {
        this.publishChannelStopped();
      }),
    );
  }

  private noteDisconnected(): void {
    this.replay = reduceDisconnected();
    this.winsizeSeen = false;
    this.stateSeen = false;
    // A drop forfeits authority: whatever happened while this client was
    // away, the reconnect handshake must arbitrate before any size leaves —
    // an ex-owner resuming from the background would otherwise re-assert its
    // stale grid at socket open and steal the PTY with zero user activity.
    // The natural-grid cache and `lastSent*` survive, so the handshake `w`
    // restores owner via the own-echo branch (or solo via the count) without
    // a wasted frame, and the v7 replay-end fallback still promotes. A
    // deliberate suspend routes through here too and forfeits identically.
    this.setAuthority('follower');
    patchState(this.state, { connection: this.replay.connection });
    this.syncStdinGate();
  }

  /**
   * `channelStopped` means the reconnect loop gave up and nothing will re-arm
   * it — the state that earns a Reconnect button. A deliberate suspend lands in
   * the store as `stopped` too, since `disconnect()` is the only way to stop
   * the bytes, but it is not a failure: the socket reopens on its own the
   * moment the session is read again, and offering a button over it would
   * invite the user to pay for a replay they did not need.
   */
  private publishChannelStopped(): void {
    patchState(this.state, {
      channelStopped:
        !this.suspended &&
        useConnectionStore.getState().channels[this.key]?.status === 'stopped',
    });
  }

  /**
   * A session is watched only while this tab is on screen *and* this is the
   * terminal the route is rendering. `MAX_RETAINED` keeps three models attached
   * while at most one is being read, so an off-screen model in a visible tab is
   * as unwatched as anything in a hidden tab.
   */
  private wireWatchGate(): void {
    this.syncWatched();
    this.disposers.push(
      onTabVisibility(() => {
        this.syncWatched();
      }),
    );
    this.disposers.push(
      onMountedTerminal(() => {
        this.syncWatched();
      }),
    );
  }

  private syncWatched(): void {
    if (this.disposed) return;
    if (!tabHidden() && mountedTerminal() === this.key) {
      this.unwatchedSince = null;
      this.unwatchedBytes = 0;
      this.resume();
      return;
    }
    // Re-baselined on the transition, not on suspend: bytes spent while the
    // user was reading are not the background's to answer for, and without this
    // a long-lived session would carry its whole history into the first hide
    // and suspend instantly.
    if (this.unwatchedSince === null) {
      this.unwatchedSince = this.now();
      this.unwatchedBytes = 0;
    }
  }

  private chargeInbound(bytes: number): void {
    if (this.suspended || this.disposed) return;
    const since = this.unwatchedSince;
    if (since === null) return;
    this.unwatchedBytes += bytes;
    // Never mid-replay: tearing one down wastes bytes already paid for and
    // strands `replayPainting`, which only an `'e'` this span will never send
    // could clear.
    if (this.replay.inReplay || this.pendingReplayWrites > 0) return;
    const spend = { unwatchedMs: this.now() - since, bytes: this.unwatchedBytes };
    if (this.suspendPolicy(spend)) this.suspend();
  }

  /**
   * Stop consuming this session's PTY output.
   *
   * Closing the socket is the only lever: the session protocol has no pause
   * frame and the relay routes `CH_SESSION_IO` solely to clients registered on
   * `session_io:<sid>`. Detaching is free on the agent — its
   * `_handle_mobile_detach` only logs, the PTY keeps running and the ring keeps
   * filling, so the next open replays everything missed.
   */
  suspend(): void {
    if (this.suspended || this.disposed) return;
    // Raised before the disconnect: the store write that triggers reaches
    // `publishChannelStopped`, which has to see the latch already set.
    this.suspended = true;
    patchState(this.state, { suspended: true });
    this.io.channel.disconnect();
    // `disconnect()` nulls the socket's handlers before closing it, so no
    // `disconnected` event will ever arrive to shut the send gate. Without this
    // the model stays `live`, `disableStdin` stays false, and every keystroke
    // is swallowed after xterm has already snapped the viewport to the tail and
    // cleared the selection.
    this.noteDisconnected();
    // Explicit, not left to the store subscription: `connectionStore.sameState`
    // swallows an identical `{stopped, 0, null}` write, so a channel that had
    // already given up would keep its Reconnect button raised through a
    // suspend.
    this.publishChannelStopped();
  }

  resume(): void {
    if (!this.suspended || this.disposed) return;
    this.suspended = false;
    this.unwatchedBytes = 0;
    // The clock as well as the counter. `retryConnection()` reaches here on a
    // model that is still unwatched, and leaving the old stamp standing would
    // let the next `RECOUP_BYTES` re-suspend it at once — the opposite of a
    // user-driven retry outranking the governor.
    if (this.unwatchedSince !== null) this.unwatchedSince = this.now();
    patchState(this.state, { suspended: false });
    // `connect()`, not `forceReconnect()`: the latter early-returns while
    // `shouldReconnect` is false, which is exactly what `disconnect()` left.
    this.io.channel.connect();
    this.publishChannelStopped();
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
          // Each replay span is its own winsize generation: a same-socket
          // handshake replacement (relay swapping a v8 agent for a v7 one)
          // must not inherit the previous handshake's `w`, or replay-end
          // would skip the v7 fallback and strand a follower. A v8 agent
          // re-emits `w` inside the span on both transports, so this reset
          // never misfires against one.
          this.winsizeSeen = false;
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
          this.stateSeen = true;
          this.state.setState({ status: decodeTerminalState(effect.json) });
          break;
        case 'replayFinished':
          this.replayEndSeen = true;
          // The replay is sunk cost already paid. Charging it would make the
          // first live byte after any reconnect-while-unwatched blow the budget
          // instantly, suspend again, and loop.
          this.unwatchedBytes = 0;
          this.settleReplayPaint();
          // No `w` frame by replay-end means a v7 agent, which cannot have
          // another size authority. Revert to solo; the `enterLive` effect
          // that always follows in this batch does the actual re-assert.
          if (!this.winsizeSeen && this.authority === 'follower') {
            this.setAuthority('solo');
          }
          break;
        case 'enterLive':
          this.assertSize();
          break;
        case 'winsize':
          this.onWinsize(effect.rows, effect.cols, effect.attachedClients);
          break;
      }
    }
    patchState(this.state, { connection: this.replay.connection });
    this.syncStdinGate();
  }

  /**
   * The latest-active arbitration state machine (proto-v8; identical on
   * Android and iOS). The agent broadcasts `w` on every effective resize and
   * every attach/detach; whoever's grid it names is the owner, everyone else
   * follows, and a count of one restores the pre-v8 sole-authority behavior.
   */
  private onWinsize(rows: number, cols: number, attachedClients: number): void {
    // The relay adds an attaching socket to the session's broadcast set
    // before the agent has registered it, so a broadcast `w` (say, the still
    // solo active client rotating) can arrive here ahead of this socket's own
    // handshake carrying `attachedClients: 1` — acting on it would grant
    // pre-handshake solo and steal the grid. The handshake always leads with
    // a state frame, so anything before one is ignored.
    if (!this.stateSeen) return;
    this.winsizeSeen = true;
    patchState(this.state, { attachedClients });
    if (attachedClients <= 1) {
      this.setAuthority('solo');
      // A fresh PTY (24×80) or a departed owner's leftover grid: deliver the
      // natural size. Equal grids stay silent — a solo reconnect costs zero
      // resize frames now that the handshake reports the PTY size.
      if (this.lastRows > 0 && (rows !== this.lastRows || cols !== this.lastCols)) {
        this.assertSize();
      }
      return;
    }
    if (
      // Only a grid this client actually PUT ON THE WIRE can echo back as
      // ownership — never the measurement cache. The cache seeds while
      // follower (a merely-measured grid must not read as a claim) and runs
      // a debounce ahead of the wire mid-drag, where a foreign claim that
      // happens to name the transient measurement would slip through as an
      // "echo"; on reconnect the same coincidence would re-promote an
      // ex-owner whose natural grid a foreign client resized the PTY to. A
      // genuine echo always names a sent grid: `lastSent*` tracks every send
      // site, and the trailing-echo corrective below collapses the debounce
      // before a second send can leapfrog it.
      this.lastSentRows > 0 &&
      rows === this.lastSentRows &&
      cols === this.lastSentCols
    ) {
      // Our own size echoed back: this client holds authority. Matching
      // `lastSent*` rather than the cache keeps the actively resizing owner
      // promoted while the cache is a debounce ahead of the echo.
      this.setAuthority('owner');
      // Same corrective as the solo branch: a stale echo can trail a cache
      // that already moved on, and a transient demotion may have canceled the
      // debounced frame that would have delivered it — re-assert the
      // divergence now and drop any pending debounce, which could only
      // re-send the same cache.
      if (this.lastRows > 0 && (rows !== this.lastRows || cols !== this.lastCols)) {
        this.assertSize();
        this.cancelResize?.();
        this.cancelResize = null;
      }
      return;
    }
    this.followerGrid = { rows, cols };
    // A foreign grid voids any historical send claim: `lastSent*` surviving
    // this demotion would let a later `w` that happens to name the old grid
    // re-promote a passive follower through the own-echo door — the same
    // no-live-claim defect the `lastSent > 0` guard closed for never-sent
    // grids. The socket-drop demotion deliberately KEEPS `lastSent*`;
    // reconnect owner-restoration depends on it.
    this.lastSentRows = 0;
    this.lastSentCols = 0;
    if (this.authority === 'follower') {
      this.solveFollowerLayout();
    } else {
      this.setAuthority('follower');
    }
  }

  private setAuthority(next: SizeAuthority): void {
    if (this.authority === next) return;
    const wasFollower = this.authority === 'follower';
    this.authority = next;
    patchState(this.state, { sizeAuthority: next });
    this.io.setResendSuppressed(next === 'follower');
    if (next === 'follower') {
      // A queued debounced assert must not fire after the demotion.
      this.cancelResize?.();
      this.cancelResize = null;
      this.solveFollowerLayout();
    } else if (wasFollower) {
      this.followerGrid = null;
      this.cancelFocusTakeover?.();
      this.cancelFocusTakeover = null;
      this.pendingFocusTakeover = false;
      this.restoreNativeLayout();
    }
  }

  /**
   * Follower → owner: recompute the natural grid at the native font and
   * assert it immediately (no debounce — a takeover triggered by typing must
   * put the resize on the wire before the keystroke's effect renders).
   */
  private takeoverNow(): void {
    if (this.authority !== 'follower') return;
    if (!this.canSendBytes()) return;
    this.setAuthority('owner');
    this.assertSize();
    this.cancelResize?.();
    this.cancelResize = null;
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
    // While following, nothing is ever *sent* from here — but the cache rules
    // differ by phase. With a foreign grid adopted, the cache stays frozen at
    // the last natural grid: that freeze is the echo suppression (our own
    // `term.resize` to the foreign grid lands here and dies) and it means a
    // takeover's `assertSize()` delivers the grid this client would have
    // fitted, with no cache reset needed. Before any grid is adopted (the
    // fresh-model state), the report IS this client's natural grid and must
    // be recorded, or the solo/v7 promotion would find an empty cache and
    // never deliver the initial size — mobile has no post-promotion
    // re-measure to heal that (the PTY would sit at 24×80), so all three
    // clients seed identically here.
    if (this.authority === 'follower') {
      if (this.followerGrid === null) {
        this.lastRows = rows;
        this.lastCols = cols;
      }
      return;
    }
    if (rows === this.lastRows && cols === this.lastCols) return;
    this.lastRows = rows;
    this.lastCols = cols;
    this.cancelResize?.();
    this.cancelResize = this.scheduler.schedule(() => {
      this.cancelResize = null;
      this.assertSize();
    }, RESIZE_DEBOUNCE_MS);
  }

  /** Re-deliver the cached grid — unless another client owns the size. */
  private assertSize(): void {
    if (this.authority === 'follower') return;
    if (this.lastRows <= 0 || this.lastCols <= 0) return;
    this.lastSentRows = this.lastRows;
    this.lastSentCols = this.lastCols;
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
    const sample = this.currentSample();
    const next = foldSample(previous, sample, userDriven);
    this.follow = next;
    // Ahead of the early return below: the thumb tracks the buffer even while
    // pinned, where neither `pinned` nor `unread` ever changes.
    this.publishScrollbar(sample);
    if (next.pinned === previous.pinned && next.unread === previous.unread) return;
    this.state.setState({ pinned: next.pinned, unread: next.unread });
  }

  private publishScrollbar(sample: ScrollSample): void {
    const next = scrollbarView(sample, this.term.rows, {
      altBuffer: this.term.buffer.active.type === 'alternate',
      mouseReporting: this.term.modes.mouseTrackingMode !== 'none',
    });
    if (sameScrollbarView(this.scrollbar.getState(), next)) return;
    this.scrollbar.setState(next);
  }

  /**
   * Scroll a program that owns its own viewport, by handing xterm wheel events.
   *
   * The alternate screen has no scrollback and a program with mouse tracking on
   * takes the wheel even without one, so there is nothing here for
   * `scrollLines` to move — mobile solves it by sending wheel buttons 64/65 or
   * cursor keys (iOS `TerminalWheelScroll`, Android `doScroll`). xterm already
   * does exactly that ladder for a wheel event, including picking the active
   * mouse *encoding* — X10 vs SGR vs urxvt, which no public API exposes — and
   * honouring DECCKM for the arrow arm. So the control synthesizes the event
   * rather than encoding a byte, and correctness stays xterm's problem.
   *
   * `deltaMode` is LINE with a unit delta because xterm's own accumulator is
   * only a non-zero gate on that path: one event is one step, exactly.
   */
  relativeScroll(rows: number): void {
    const steps = relativeSteps(rows, this.term.rows);
    if (steps === 0 || !this.canSendBytes()) return;
    const element = this.term.element;
    if (element === undefined) return;
    const screen = element.querySelector('.xterm-screen');
    if (screen === null) return;
    // A report carries the cell under the pointer, and coordinates outside the
    // grid are dropped — so aim inside it rather than at the track that was
    // actually dragged. Pinned per call, like the mobile anchor.
    const box = screen.getBoundingClientRect();
    const clientX = box.left + Math.min(60, box.width / 2);
    const clientY = box.top + Math.min(60, box.height / 2);
    const deltaY = rows > 0 ? 1 : -1;
    for (let step = 0; step < steps; step += 1) {
      screen.dispatchEvent(
        new WheelEvent('wheel', {
          deltaY,
          deltaMode: WheelEvent.DOM_DELTA_LINE,
          clientX,
          clientY,
          bubbles: true,
          cancelable: true,
        }),
      );
    }
  }

  /** Rows a drag of `pixels` is worth, carrying the sub-row remainder. */
  relativeScrollByPixels(pixels: number): void {
    const { rows, carry } = wheelRows(pixels, this.cellHeight(), this.wheelCarry);
    this.wheelCarry = carry;
    this.relativeScroll(rows);
  }

  /** A screenful, which is also `relativeScroll`'s per-call ceiling. */
  relativeScrollPage(pages: number): void {
    this.relativeScroll(pages * Math.max(1, this.term.rows));
  }

  /**
   * Move the viewport on the user's behalf.
   *
   * Every one of these arms `rebaseline` before it scrolls, so the tally reads
   * the move as the user's rather than as rows they missed, and calls
   * `sampleFollow()` afterwards — xterm emits no `onScroll` for a scroll that
   * lands where it already was, and the flag has to be consumed either way or
   * it would suppress the next genuine accrual.
   */
  jumpToBottom(): void {
    this.rebaseline = true;
    this.term.scrollToBottom();
    this.sampleFollow();
  }

  scrollToTop(): void {
    this.rebaseline = true;
    this.term.scrollToTop();
    this.sampleFollow();
  }

  /** Seek the slider, as a fraction of the scrollback. */
  scrollToOffset(offset: number): void {
    this.rebaseline = true;
    this.term.scrollToLine(lineForOffset(offset, this.term.buffer.active.baseY));
    this.sampleFollow();
  }

  scrollByLines(lines: number): void {
    if (lines === 0) return;
    this.rebaseline = true;
    this.term.scrollLines(lines);
    this.sampleFollow();
  }

  scrollByPages(pages: number): void {
    if (pages === 0) return;
    this.rebaseline = true;
    this.term.scrollPages(pages);
    this.sampleFollow();
  }

  /**
   * Scroll by a pixel-mode wheel delta, in rows.
   *
   * The conversion belongs here rather than in the view because the cell height
   * does: the client is the sole size authority, so the grid it fitted is what
   * says how tall a row is.
   */
  scrollByPixels(deltaY: number): void {
    const { rows, carry } = wheelRows(deltaY, this.cellHeight(), this.wheelCarry);
    this.wheelCarry = carry;
    this.scrollByLines(rows);
  }

  /** Row height in CSS pixels, or 0 before the host has been laid out. */
  private cellHeight(): number {
    const rows = this.term.rows;
    if (rows <= 0) return 0;
    // Measure the grid itself, not the host: in follower mode the emulator is
    // letterboxed inside the container, and dividing the host height by a
    // foreign row count would over-estimate every drag's row conversion.
    const screen = this.term.element?.querySelector('.xterm-screen');
    const height =
      screen instanceof HTMLElement && screen.clientHeight > 0
        ? screen.clientHeight
        : this.host.clientHeight;
    if (height <= 0) return 0;
    return height / rows;
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
    // A user-driven retry outranks the governor. Leaving the latch set would
    // pin `chargeInbound` at its early return, so the session could never be
    // suspended again.
    this.resume();
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
    // The gate opening is exactly the moment a parked focus claim can act.
    if (!disabled) this.maybeRetryFocusTakeover();
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
  private sendPayload(kind: 'input' | 'execute', outcome: PayloadOutcome): boolean {
    if (outcome.kind === 'empty') return false;
    if (outcome.kind === 'tooLarge') {
      useToastStore
        .getState()
        .show('That is over the 1 MB limit for one message and was not sent.', 'error');
      return false;
    }
    return this.io.send(terminalTextFrame(kind, outcome.text));
  }

  /**
   * Returns whether a frame actually went out, so a caller can refuse to
   * spend anything irreversible — a selection, an open picker — on a paste
   * that was silently gated or refused over the cap.
   */
  pasteText(text: string): boolean {
    if (!this.canSendBytes()) return false;
    // A paste is user input, so it claims size authority like typing does.
    this.takeoverNow();
    // One frame, not Android's three: these cross a WebSocket and a relay, and
    // losing only the closing marker would strand the program's line editor in
    // paste-collect mode and read as a dead keyboard.
    return this.sendPayload('input', encodePaste(text, this.term.modes.bracketedPasteMode));
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
    // The mobile row-tap this stands in for is a takeover trigger there too.
    this.takeoverNow();
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

  /**
   * Paste the current selection onto the program's input line.
   *
   * The desktop stand-in for mobile's send-to-composer: the text lands in the
   * running program's line editor for review, so nothing executes until the
   * user submits it themselves.
   */
  pasteSelection(): void {
    // Settledness first so a mid-paint click gets the same explanation the
    // other two actions give. The send gate then refuses silently: the view
    // mirrors it into the button's `canPaste` prop, so this backstop only
    // catches a state change between pointerdown and mouseup — and it keeps
    // the selection intact when it does.
    if (!this.screenIsSettled() || !this.canSendBytes()) return;
    const text = this.term.getSelection();
    if (text === '') return;
    // Only a frame that actually went out may cost the user their selection —
    // an over-cap refusal has already toasted and leaves the retry target.
    if (!this.pasteText(text)) return;
    this.term.clearSelection();
    // The paste bypasses xterm's user-input path and `focus()` preserves
    // scroll, so without the jump an insert made while scrolled up — the
    // normal state when selecting older output — lands off-screen.
    this.jumpToBottom();
    // Same reason as `runSelection`: the toolbar button that was clicked is
    // about to leave the DOM.
    this.focus();
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

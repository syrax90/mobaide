/**
 * Reconnecting WebSocket channel — the web port of Android
 * `WebSocketConnection` and iOS `WebSocketCore`.
 *
 * - Backoff sleeps the current ladder value, then doubles it (cap 30 s, plus
 *   web-only jitter). A successful open and `forceReconnect()` reset it.
 * - A generation counter — incremented **only** by `forceReconnect()`, matching
 *   the mobile code rather than Android's KDoc — drops every event from a
 *   superseded socket, so a stale one cannot emit a phantom `disconnected` or
 *   trigger a reconnect.
 * - `buildUrl` is re-evaluated on every (re)connect attempt, so a rotated token
 *   is picked up without rebuilding the channel. Resolving to `null` means
 *   different things per axis: on the user axis it is "signed out" and stops,
 *   while on the agent axis it usually means the network token mint failed, so
 *   it retries — bounded, because a permanent 403 looks identical. Throwing
 *   emits `error` and stops.
 * - Terminal outcomes ride the event stream (iOS's shape) instead of Android's
 *   constructor callbacks.
 * - Subscribe before calling `connect()`: the whole cold-start burst —
 *   `connected` plus the server's opening snapshot — arrives on the first tick.
 *
 * Everything the browser cannot provide is injected, so the state machine is
 * unit-testable with no network and no real timers.
 */
import { WS_CLOSE } from '../protocol/closeCodes';
import { Backoff } from './backoff';
import { closePolicyFor, type ChannelAxis, type TerminalOutcome } from './closePolicy';

export type ChannelEvent =
  | { kind: 'connected' }
  | { kind: 'disconnected' }
  | { kind: 'message'; data: string }
  | { kind: 'binary'; data: ArrayBuffer }
  | { kind: 'error'; message: string }
  | TerminalOutcome;

export type ChannelStatus = 'idle' | 'connecting' | 'connected' | 'stopped';

export interface ChannelState {
  status: ChannelStatus;
  /** Consecutive failed attempts since the last successful open. */
  attempt: number;
  lastCloseCode: number | null;
}

/** The slice of the browser `WebSocket` this channel uses. */
export interface WebSocketLike {
  readonly readyState: number;
  binaryType: string;
  send(data: string | ArrayBufferView): void;
  close(code?: number, reason?: string): void;
  onopen: (() => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  onclose: ((event: { code: number; reason: string }) => void) | null;
  onerror: (() => void) | null;
}

/**
 * Timer seam. `schedule` returns its own cancel function so no platform-specific
 * handle type leaks into the channel or its tests.
 */
export interface Scheduler {
  schedule(fn: () => void, delayMs: number): () => void;
}

export const realScheduler: Scheduler = {
  schedule(fn, delayMs) {
    const id = setTimeout(fn, delayMs);
    return () => {
      clearTimeout(id);
    };
  },
};

export type ChannelSend = (data: string | Uint8Array) => boolean;

export interface WebSocketChannelOptions {
  /** Stable identity used as the connection-store key, e.g. `agent-events:A1`. */
  name: string;
  axis: ChannelAxis;
  /** Full `ws(s)://…?token=…`, or null when the channel is not connectable now. */
  buildUrl: () => Promise<string | null>;
  /** Set `binaryType = 'arraybuffer'` — the session I/O channel needs it. */
  binary?: boolean;
  /**
   * Runs on every open *before* `connected` is emitted, so a resume frame is
   * the first thing on the wire (the auto-scan subscribe window is 200 ms
   * direct / 1 s through the relay).
   */
  onOpen?: (send: ChannelSend) => void;
  onStateChange?: (name: string, state: ChannelState) => void;
  /** Invoked when the close policy says the cached agent token is stale. */
  onClearAgentToken?: () => void;
  socketFactory?: (url: string) => WebSocketLike;
  scheduler?: Scheduler;
  random?: () => number;
}

const WS_READY_OPEN = 1;

/**
 * Consecutive failures to even open before the agent credential is assumed
 * stale. Above one so a plain relay outage does not burn a good token, low
 * enough that recovery costs a few seconds of backoff rather than a reload.
 */
const PRE_OPEN_FAILURES_BEFORE_REMINT = 3;

/**
 * How many times the agent axis retries a URL it could not build before giving
 * up.
 *
 * A null URL there usually means the token mint hit the network and failed, so
 * retrying is right — but `ensureAgentToken` collapses every error to null,
 * including the permanent `403 Not the owner of this agent` that follows a 4003
 * close. Without a bound that becomes one `/token` POST per backoff interval
 * forever. `connectAgentEvents` re-arms a stopped channel, so this is a pause,
 * not a dead end.
 */
const MAX_UNBUILDABLE_URL_ATTEMPTS = 5;

function defaultSocketFactory(url: string): WebSocketLike {
  return new WebSocket(url) as unknown as WebSocketLike;
}

export class WebSocketChannel {
  readonly name: string;

  private readonly options: WebSocketChannelOptions;
  private readonly socketFactory: (url: string) => WebSocketLike;
  private readonly scheduler: Scheduler;
  private readonly backoff: Backoff;
  private readonly listeners = new Set<(event: ChannelEvent) => void>();

  private socket: WebSocketLike | null = null;
  private cancelReconnect: (() => void) | null = null;
  private shouldReconnect = false;
  private connecting = false;
  private generation = 0;
  private openedThisCycle = false;
  private preOpenFailures = 0;
  private unbuildableUrlAttempts = 0;
  private state: ChannelState = { status: 'idle', attempt: 0, lastCloseCode: null };

  constructor(options: WebSocketChannelOptions) {
    this.options = options;
    this.name = options.name;
    this.socketFactory = options.socketFactory ?? defaultSocketFactory;
    this.scheduler = options.scheduler ?? realScheduler;
    this.backoff = new Backoff(options.random);
  }

  get currentState(): ChannelState {
    return this.state;
  }

  /** Attach a listener. Returns the unsubscribe. Attach before `connect()`. */
  subscribe(listener: (event: ChannelEvent) => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  /**
   * Idempotent: a second call does nothing while a connect is in flight, a
   * socket is live, or a backoff reconnect is already armed — otherwise a
   * caller re-entering during the backoff window would open a rival socket.
   */
  connect(): void {
    this.shouldReconnect = true;
    if (this.connecting || this.socket !== null || this.cancelReconnect !== null) {
      return;
    }
    void this.doConnect();
  }

  /** Stop for good. The channel can be started again with `connect()`. */
  disconnect(): void {
    this.shouldReconnect = false;
    this.cancelPendingReconnect();
    this.closeSocket('Client disconnect');
    this.backoff.reset();
    this.setState({ status: 'stopped', attempt: 0, lastCloseCode: null });
  }

  /**
   * Drop the current socket and reconnect immediately.
   *
   * The one place the generation counter advances, which is what makes the
   * superseded socket's late events harmless.
   */
  forceReconnect(): void {
    if (!this.shouldReconnect) return;
    this.generation += 1;
    this.cancelPendingReconnect();
    this.backoff.reset();
    this.closeSocket('Force reconnect');
    void this.doConnect();
  }

  send(data: string | Uint8Array): boolean {
    const socket = this.socket;
    if (socket === null || socket.readyState !== WS_READY_OPEN) {
      this.emit({ kind: 'error', message: 'WebSocket send failed' });
      return false;
    }
    socket.send(data);
    return true;
  }

  private emit(event: ChannelEvent): void {
    for (const listener of [...this.listeners]) listener(event);
  }

  private setState(next: ChannelState): void {
    this.state = next;
    this.options.onStateChange?.(this.name, next);
  }

  private cancelPendingReconnect(): void {
    this.cancelReconnect?.();
    this.cancelReconnect = null;
  }

  /**
   * Clear the in-flight flag, but only for the current generation.
   *
   * The flag is shared across generations, so a superseded attempt returning
   * from its `await` would otherwise clear it while the live attempt was still
   * pending — reopening the `connect()` guard and letting a third socket be
   * built alongside the second.
   */
  private releaseConnecting(myGeneration: number): void {
    if (myGeneration === this.generation) this.connecting = false;
  }

  private closeSocket(reason: string): void {
    const socket = this.socket;
    this.socket = null;
    if (socket === null) return;
    socket.onopen = null;
    socket.onmessage = null;
    socket.onclose = null;
    socket.onerror = null;
    try {
      // 1000 is the only non-3000–4999 code a browser client may send; anything
      // else throws InvalidAccessError.
      socket.close(WS_CLOSE.normal, reason);
    } catch {
      // Already closing or closed — nothing to undo.
    }
  }

  private async doConnect(): Promise<void> {
    const myGeneration = this.generation;
    this.connecting = true;
    this.setState({ ...this.state, status: 'connecting' });

    let url: string | null;
    try {
      url = await this.options.buildUrl();
    } catch (error) {
      this.releaseConnecting(myGeneration);
      // Same guard as the success path: a channel torn down while its token
      // mint was in flight must not write state back, or `setState` would
      // re-insert the connection-store entry `forgetChannel` just removed and
      // leave a zombie with no owner to clean it up.
      if (myGeneration !== this.generation || !this.shouldReconnect) return;
      this.emit({ kind: 'error', message: describeError(error) });
      this.setState({ ...this.state, status: 'stopped' });
      return;
    }

    this.releaseConnecting(myGeneration);
    if (myGeneration !== this.generation || !this.shouldReconnect) return;

    if (url === null) {
      this.emit({ kind: 'disconnected' });
      this.unbuildableUrlAttempts += 1;
      // The two axes mean different things by "no URL". On the user axis it is
      // the mobile semantic — logged out, nothing to reconnect to — so stop
      // rather than burn the ladder. On the agent axis the token is minted over
      // the network, so a null usually means the mint just failed and stopping
      // would strand the channel on a blip; but the mint collapses a permanent
      // 403 into the same null, so the retry has to be bounded.
      if (
        this.options.axis === 'user' ||
        this.unbuildableUrlAttempts >= MAX_UNBUILDABLE_URL_ATTEMPTS
      ) {
        this.shouldReconnect = false;
        this.setState({ ...this.state, status: 'stopped' });
        return;
      }
      this.scheduleReconnect(null);
      return;
    }
    this.unbuildableUrlAttempts = 0;

    let socket: WebSocketLike;
    try {
      socket = this.socketFactory(url);
      if (this.options.binary === true) socket.binaryType = 'arraybuffer';
    } catch (error) {
      // The `WebSocket` constructor throws `SyntaxError` synchronously for a URL
      // it rejects. (A CSP `connect-src` refusal is *asynchronous* in current
      // Chromium and arrives as a close, not a throw — so this branch is
      // narrower than it first looks.) Left unguarded the throw became an
      // unhandled rejection out of a `void this.doConnect()`: no event, no
      // reconnect, and the channel stuck reporting `connecting` for the tab's
      // lifetime.
      this.emit({ kind: 'error', message: describeError(error) });
      this.emit({ kind: 'disconnected' });
      this.scheduleReconnect(null);
      return;
    }
    // Belt and braces against an interleaving that produced two live sockets:
    // whatever is here now is superseded by the one just built.
    this.closeSocket('Superseded');
    this.socket = socket;

    let settled = false;
    const settle = (code: number, reason: string): void => {
      if (settled) return;
      settled = true;
      // Detach before handling: a dead socket must not deliver a late frame
      // into the next connection cycle, which the generation counter alone
      // would not catch (it only advances on forceReconnect).
      socket.onopen = null;
      socket.onmessage = null;
      socket.onclose = null;
      socket.onerror = null;
      this.handleClose(myGeneration, code, reason);
    };

    socket.onopen = () => {
      if (myGeneration !== this.generation) return;
      this.openedThisCycle = true;
      this.preOpenFailures = 0;
      this.backoff.reset();
      this.setState({ status: 'connected', attempt: 0, lastCloseCode: null });
      this.options.onOpen?.((data) => this.send(data));
      this.emit({ kind: 'connected' });
    };

    socket.onmessage = (event) => {
      if (myGeneration !== this.generation) return;
      const { data } = event;
      if (typeof data === 'string') {
        this.emit({ kind: 'message', data });
      } else if (data instanceof ArrayBuffer) {
        this.emit({ kind: 'binary', data });
      }
    };

    // A browser fires `error` and then `close`; funnelling both through one
    // settle guard keeps that from scheduling two reconnects, and still covers
    // a transport that only reports one of them.
    socket.onerror = () => {
      if (myGeneration !== this.generation) return;
      this.emit({ kind: 'error', message: 'WebSocket error' });
      if (this.socket?.readyState !== WS_READY_OPEN) settle(1006, '');
    };

    socket.onclose = (event) => {
      settle(event.code, event.reason);
    };
  }

  private handleClose(myGeneration: number, code: number, reason: string): void {
    if (myGeneration !== this.generation) return;
    this.socket = null;
    this.emit({ kind: 'disconnected' });

    const openedThisCycle = this.openedThisCycle;
    this.openedThisCycle = false;
    if (!openedThisCycle) this.preOpenFailures += 1;

    const policy = closePolicyFor(code, this.options.axis, reason);
    // A per-agent `mobile_access` JWT is minted without an `exp`, so
    // `ensureAgentToken`'s freshness check never rotates it — and the relay
    // rejects a bad one with 4001/4010 *before* accept, which a browser only
    // sees as 1006. Without this the channel would re-present the same dead
    // credential forever. Repeated failures to even open are the closest signal
    // a browser gets, so treat them as the rejection the close code cannot
    // deliver; the next connect re-mints.
    const rotateCredential =
      this.options.axis === 'agent' && this.preOpenFailures >= PRE_OPEN_FAILURES_BEFORE_REMINT;
    if (policy.clearAgentToken || rotateCredential) {
      this.preOpenFailures = 0;
      this.options.onClearAgentToken?.();
    }
    if (policy.terminal !== null) this.emit(policy.terminal);

    if (!policy.reconnect) {
      this.shouldReconnect = false;
      this.cancelPendingReconnect();
      this.setState({ status: 'stopped', attempt: this.state.attempt, lastCloseCode: code });
      return;
    }
    this.scheduleReconnect(code);
  }

  private scheduleReconnect(lastCloseCode: number | null): void {
    if (!this.shouldReconnect) return;
    this.cancelPendingReconnect();
    const delay = this.backoff.next();
    this.setState({
      status: 'connecting',
      attempt: this.state.attempt + 1,
      lastCloseCode,
    });
    this.cancelReconnect = this.scheduler.schedule(() => {
      this.cancelReconnect = null;
      if (!this.shouldReconnect) return;
      void this.doConnect();
    }, delay);
  }
}

/**
 * Strip the credential out of anything headed for the event stream.
 *
 * Chromium's `SyntaxError` for a URL the WebSocket constructor rejects quotes
 * the whole URL back — `?token=<live JWT>` included — and this codebase's
 * convention for an error string is to toast it into the DOM
 * (`lib/queryClient.ts`). Redacting inside `describeError` covers every emit
 * site by construction rather than per call.
 */
function redactToken(message: string): string {
  return message.replace(/([?&]token=)[^&\s'"]*/g, '$1<redacted>');
}

function describeError(error: unknown): string {
  return redactToken(error instanceof Error ? error.message : String(error));
}

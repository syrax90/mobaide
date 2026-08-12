/**
 * The four realtime channels, wired to WEB-03's credential seams.
 *
 * The SPA is same-origin with the relay (WEB-02), so every URL is built from
 * `window.location.origin`. The axis split is the one the fixtures encode:
 * agent channels go through the relay path rewrite, while the user-events
 * channel is built in `direct` mode *against the same origin* and is therefore
 * never rewritten.
 *
 * Tokens are resolved per connect attempt, inside `buildUrl`, because a WS URL
 * carries its credential in the query string and cannot be re-authenticated
 * mid-handshake. That is also the only place a dead session can be noticed on
 * this axis: browsers expose no upgrade status, so a rejected token arrives as
 * an ordinary abnormal close.
 */
import { getAccessToken } from '../auth/sessionTransport';
import { clearAgentToken, ensureAgentToken } from '../auth/agentTokens';
import { useSessionStore } from '../auth/sessionStore';
import { ensureFreshUserToken } from '../auth/userTokens';
import { AutoScanCursor, buildSubscribeMessage } from '../protocol/autoscanFrames';
import { buildWebSocketUrl } from '../protocol/relayUrls';
import {
  encodeTerminalFrame,
  type TerminalOutboundFrame,
} from '../protocol/terminalCodec';
import { useConnectionStore } from './connectionStore';
import { registerDisposable, unregisterDisposable } from './liveChannels';
import {
  WebSocketChannel,
  type ChannelState,
  type WebSocketChannelOptions,
} from './webSocketChannel';

/** Same-origin base; the Vite dev proxy and the production Caddy site both front the relay. */
function originBase(): string {
  return window.location.origin;
}

function publishState(name: string, state: ChannelState): void {
  useConnectionStore.getState().setChannelState(name, state);
}

/**
 * Close a channel and drop its connection-store entry.
 *
 * Both halves matter: without the disconnect a PTY or auto-scan socket outlives
 * sign-out still authorised (the relay only authenticates at handshake), and
 * without `forgetChannel` the store accumulates one dead entry per session ever
 * opened, for the life of the tab.
 */
function disposeChannel(owner: { dispose: () => void }, channel: WebSocketChannel): void {
  unregisterDisposable(owner);
  channel.disconnect();
  useConnectionStore.getState().forgetChannel(channel.name);
}

type ChannelOverrides = Partial<
  Pick<WebSocketChannelOptions, 'socketFactory' | 'scheduler' | 'random'>
>;

async function userToken(): Promise<string | null> {
  // Pre-emptive, like the mobile URL providers: a token that expires seconds
  // after the handshake would strand the socket until the next close.
  //
  // The outcome has to be honoured, not discarded. `/api/auth/refresh` is in
  // `UNAUTHENTICATED_PATHS`, so its 401 never reaches the REST teardown — and
  // the close code that would say so never reaches a browser either (4001 is
  // sent pre-accept and arrives as 1006). This is the only place a dead
  // session is observable on this axis; without it the channel would reconnect
  // forever presenting a token the server has already rejected, behind a UI
  // that still looks signed in. Only `terminal` acts: `transient` is a flaky
  // network and must not sign anyone out.
  const outcome = await ensureFreshUserToken();
  if (outcome.kind === 'terminal') {
    useSessionStore.getState().invalidateUserSession('tokenRejected');
    return null;
  }
  return getAccessToken();
}

export const USER_EVENTS_CHANNEL = 'user-events';

export function createUserEventsChannel(overrides: ChannelOverrides = {}): WebSocketChannel {
  return new WebSocketChannel({
    name: USER_EVENTS_CHANNEL,
    axis: 'user',
    buildUrl: async () => {
      const token = await userToken();
      if (token === null) return null;
      return buildWebSocketUrl({
        base: originBase(),
        mode: 'direct',
        path: 'ws/user-events',
        token,
      });
    },
    onStateChange: publishState,
    ...overrides,
  });
}

export function agentEventsChannelName(agentId: string): string {
  return `agent-events:${agentId}`;
}

export function createAgentEventsChannel(
  agentId: string,
  overrides: ChannelOverrides = {},
): WebSocketChannel {
  return new WebSocketChannel({
    name: agentEventsChannelName(agentId),
    axis: 'agent',
    buildUrl: async () => {
      const token = await ensureAgentToken(agentId);
      if (token === null) return null;
      return buildWebSocketUrl({
        base: originBase(),
        mode: 'relay',
        path: 'ws/events',
        agentId: encodeURIComponent(agentId),
        token,
      });
    },
    onStateChange: publishState,
    onClearAgentToken: () => {
      clearAgentToken(agentId);
    },
    ...overrides,
  });
}

export function sessionChannelName(agentId: string, sessionId: string): string {
  return `session:${agentId}:${sessionId}`;
}

/**
 * Binary PTY channel.
 *
 * The client is the sole PTY size authority: the agent spawns every session at
 * 24×80 and never reports a size back, so the measured grid has to be
 * re-asserted on every open or a reconnect silently leaves a full-screen TUI
 * drawing into 24 rows. `onOpen` covers the socket-open case from this channel's
 * own cache; WEB-05 owns the replay state machine and its two extra re-assert
 * points (first inbound binary while reconnecting, and `replayEnd`), which it
 * drives through `setSize` because its model caches the measured grid *before*
 * the resize debounce and can therefore be ahead of this cache.
 */
export class SessionIoChannel {
  readonly channel: WebSocketChannel;
  private rows = 0;
  private cols = 0;
  /**
   * Raised while the session model is a size *follower* (proto-v8 latest-
   * active arbitration): a reconnect must then not re-assert the stale
   * natural grid ahead of the handshake's authoritative `w` frame.
   */
  private resendSuppressed = false;

  constructor(agentId: string, sessionId: string, overrides: ChannelOverrides = {}) {
    this.channel = new WebSocketChannel({
      name: sessionChannelName(agentId, sessionId),
      axis: 'agent',
      binary: true,
      buildUrl: async () => {
        const token = await ensureAgentToken(agentId);
        if (token === null) return null;
        return buildWebSocketUrl({
          base: originBase(),
          mode: 'relay',
          path: `ws/session/${encodeURIComponent(sessionId)}`,
          agentId: encodeURIComponent(agentId),
          token,
        });
      },
      onOpen: () => {
        this.resendCachedSize();
      },
      onStateChange: publishState,
      onClearAgentToken: () => {
        clearAgentToken(agentId);
      },
      ...overrides,
    });
    registerDisposable(this);
  }

  send(frame: TerminalOutboundFrame): boolean {
    return this.channel.send(encodeTerminalFrame(frame));
  }

  /** Cache the measured grid and push it now. Callers debounce; this does not. */
  setSize(rows: number, cols: number): void {
    this.rows = Math.max(1, rows);
    this.cols = Math.max(1, cols);
    this.send({ kind: 'resize', rows: this.rows, cols: this.cols });
  }

  /** Gate the socket-open re-assert; an explicit `setSize` is unaffected. */
  setResendSuppressed(suppressed: boolean): void {
    this.resendSuppressed = suppressed;
  }

  /** Re-push the last measured grid. No-op before the first measurement. */
  private resendCachedSize(): void {
    if (this.resendSuppressed) return;
    if (this.rows <= 0 || this.cols <= 0) return;
    this.send({ kind: 'resize', rows: this.rows, cols: this.cols });
  }

  dispose(): void {
    disposeChannel(this, this.channel);
  }
}

export function autoscanChannelName(agentId: string): string {
  return `autoscan:${agentId}`;
}

/**
 * Auto-scan stream.
 *
 * The subscribe frame is the first thing sent on every open — the agent only
 * waits 200 ms for it directly, 1 s through the relay — and it is rebuilt from
 * the live wire cursor, so a reconnect resumes at the true high-water mark
 * instead of the value captured when the channel was created.
 */
export class AutoScanChannel {
  readonly channel: WebSocketChannel;
  readonly cursor: AutoScanCursor;
  private subscribe: readonly string[] = [];

  constructor(
    agentId: string,
    options: {
      subscribe?: readonly string[];
      resumeFromSeq?: Readonly<Record<string, bigint>>;
    } & ChannelOverrides = {},
  ) {
    const { subscribe = [], resumeFromSeq = {}, ...overrides } = options;
    this.subscribe = subscribe;
    this.cursor = new AutoScanCursor(resumeFromSeq);
    this.channel = new WebSocketChannel({
      name: autoscanChannelName(agentId),
      axis: 'agent',
      buildUrl: async () => {
        const token = await ensureAgentToken(agentId);
        if (token === null) return null;
        return buildWebSocketUrl({
          base: originBase(),
          mode: 'relay',
          path: 'ws/autoscan',
          agentId: encodeURIComponent(agentId),
          token,
        });
      },
      onOpen: (send) => {
        send(buildSubscribeMessage(this.subscribe, this.cursor));
      },
      onStateChange: publishState,
      onClearAgentToken: () => {
        clearAgentToken(agentId);
      },
      ...overrides,
    });
    registerDisposable(this);
  }

  /**
   * Open (or re-open) the socket, re-registering as a disposable first.
   *
   * The re-register is what makes reviving a disposed channel safe. Callers
   * re-arm this object after `stopAllRealtime()` rather than building a new one,
   * and `dispose()` has already removed it from the registry by then — so
   * reaching for `channel.connect()` directly would put a live, still-authorised
   * socket somewhere `stopAllRealtime()` can no longer see. `registerDisposable`
   * is a `Set.add`, so calling this on a healthy channel is a no-op.
   */
  connect(): void {
    registerDisposable(this);
    this.channel.connect();
  }

  /** Replace the subscription set; the server treats each frame as a full replace. */
  setSubscribe(projects: readonly string[]): void {
    this.subscribe = [...projects];
    this.channel.send(buildSubscribeMessage(this.subscribe, this.cursor));
  }

  dispose(): void {
    disposeChannel(this, this.channel);
  }
}

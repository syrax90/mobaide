/**
 * Where the user session lives, and how tabs keep it in sync.
 *
 * Storage is `sessionStorage`, not `localStorage`: the dashboard JWT is a
 * bearer the server cannot revoke (there is no JWT denylist), so keeping it
 * per-tab and off disk is the whole blast-radius story. The cost is that a new
 * tab starts with nothing — which is what the `BroadcastChannel` below fixes,
 * by asking already-open tabs to hand the session over.
 *
 * The channel is not only a convenience. Refresh tokens rotate on use, so once
 * one tab refreshes, every other tab's copy is dead. Broadcasting the rotated
 * session is what keeps them alive.
 */
import type { InvalidationReason } from './authState';

const STORAGE_KEY = 'mobaide-session';
const CHANNEL_NAME = 'mobaide-session';

/**
 * How long a booting tab waits for a peer to answer its handover request.
 * Long enough for a peer's message loop to turn over, short enough that a
 * genuinely first tab is not held on a splash screen.
 */
const HANDOFF_TIMEOUT_MS = 300;

export interface UserSession {
  accessToken: string;
  refreshToken: string | null;
  userId: string;
  email: string;
  role: string;
  scheduledDeletionAt: string | null;
}

type ChannelMessage =
  | { type: 'request' }
  | { type: 'offer'; session: UserSession }
  | { type: 'update'; session: UserSession }
  | { type: 'logout'; reason: InvalidationReason };

/**
 * `sessionStorage` is an untrusted boundary — another script on the origin, or
 * a stale shape from an older build, must not flow into an Authorization
 * header as `undefined`. Same defensive posture as `themeStore`'s persist
 * `merge` whitelist.
 */
function parseSession(raw: string | null): UserSession | null {
  if (raw === null) return null;
  try {
    const value: unknown = JSON.parse(raw);
    return isSession(value) ? value : null;
  } catch {
    return null;
  }
}

function isSession(value: unknown): value is UserSession {
  if (typeof value !== 'object' || value === null) return false;
  const s = value as Record<string, unknown>;
  return (
    typeof s.accessToken === 'string' &&
    s.accessToken.length > 0 &&
    (typeof s.refreshToken === 'string' || s.refreshToken === null) &&
    typeof s.userId === 'string' &&
    typeof s.email === 'string' &&
    typeof s.role === 'string' &&
    (typeof s.scheduledDeletionAt === 'string' || s.scheduledDeletionAt === null)
  );
}

// Memory is the source of truth for reads on the hot path (every request runs
// through the bearer middleware); sessionStorage is the durable mirror.
let current: UserSession | null = null;

function persist(session: UserSession | null): void {
  try {
    if (session === null) {
      sessionStorage.removeItem(STORAGE_KEY);
    } else {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session));
    }
  } catch {
    // Storage denied: the session still works for this tab's lifetime.
  }
}

let channel: BroadcastChannel | null = null;

function getChannel(): BroadcastChannel | null {
  if (channel !== null) return channel;
  if (typeof BroadcastChannel === 'undefined') return null;
  channel = new BroadcastChannel(CHANNEL_NAME);
  return channel;
}

function post(message: ChannelMessage): void {
  getChannel()?.postMessage(message);
}

type RemoteHandler = (message: ChannelMessage) => void;

let remoteHandler: RemoteHandler | null = null;

/**
 * Wire the channel up. Called once by the session store, which supplies the
 * handler that applies remote updates to its own state.
 *
 * Answering `request` is unconditional for any tab holding a session: this
 * origin is the trust boundary, and a script able to post on the channel is
 * already able to read `sessionStorage` in its own tab. The handover buys
 * durability (nothing on disk), not XSS resistance.
 */
export function connectSessionChannel(handler: RemoteHandler): void {
  remoteHandler = handler;
  const ch = getChannel();
  if (ch === null) return;
  ch.onmessage = (event: MessageEvent<ChannelMessage>) => {
    const message = event.data;
    if (message.type === 'request') {
      if (current !== null) {
        post({ type: 'offer', session: current });
      }
      return;
    }
    if (message.type === 'offer') {
      // Only useful while booting; `hydrate` installs its own listener for it.
      return;
    }
    remoteHandler?.(message);
  };
}

export function readSession(): UserSession | null {
  return current;
}

export function getAccessToken(): string | null {
  return current?.accessToken ?? null;
}

/** Adopt a session locally and tell other tabs, so rotation propagates. */
export function writeSession(session: UserSession, broadcast = true): void {
  current = session;
  persist(session);
  if (broadcast) post({ type: 'update', session });
}

/** Adopt a session without telling anyone — for applying a remote update. */
export function adoptSession(session: UserSession | null): void {
  current = session;
  persist(session);
}

export function clearSession(reason: InvalidationReason, broadcast = true): void {
  current = null;
  persist(null);
  if (broadcast) post({ type: 'logout', reason });
}

/**
 * Resolve this tab's session at boot: own storage first, then a peer handover.
 *
 * Async by necessity, which is why the session store exposes a `hydrated` flag
 * and the app holds a splash until this settles — rendering the login screen
 * first would flash it for every new tab of a signed-in user.
 */
export async function hydrateSession(): Promise<UserSession | null> {
  let raw: string | null = null;
  try {
    raw = sessionStorage.getItem(STORAGE_KEY);
  } catch {
    raw = null;
  }
  const own = parseSession(raw);
  if (own !== null) {
    current = own;
    return own;
  }

  const ch = getChannel();
  if (ch === null) return null;

  const offered = await new Promise<UserSession | null>((resolve) => {
    let settled = false;
    const finish = (session: UserSession | null) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      ch.removeEventListener('message', onMessage);
      resolve(session);
    };
    const onMessage = (event: MessageEvent<ChannelMessage>) => {
      const message = event.data;
      if (message.type === 'offer' && isSession(message.session)) {
        finish(message.session);
      }
    };
    const timer = setTimeout(() => {
      finish(null);
    }, HANDOFF_TIMEOUT_MS);
    ch.addEventListener('message', onMessage);
    post({ type: 'request' });
  });

  if (offered !== null) {
    current = offered;
    persist(offered);
  }
  return offered;
}

/** Test seam — drops in-process state and tears the channel down. */
export function resetSessionTransport(): void {
  current = null;
  remoteHandler = null;
  channel?.close();
  channel = null;
}

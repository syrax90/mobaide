/**
 * Where the user session lives, and how tabs keep it in sync.
 *
 * Two credentials with two different homes. The access token stays in
 * `sessionStorage` — per-tab and off disk — because it is a bearer the server
 * cannot revoke (there is no JWT denylist), so a narrow blast radius is all
 * that protects it. The *refresh* token is not here at all: it lives in an
 * HttpOnly cookie the page cannot read, which is what lets a session outlive
 * the last tab, and which means an XSS costs one tab's 15-minute token rather
 * than a 30-day renewable session.
 *
 * The `BroadcastChannel` survives that move for two reasons. It hands a
 * session to a new tab without a round trip, and refresh tokens rotate on use,
 * so broadcasting a rotation is what keeps every other tab's access token
 * fresh.
 *
 * Deliberately network-free: `userTokens` imports this module, so a fetch here
 * would close the cycle that `api/auth.ts` and `install.ts` exist to break.
 * Hydration reports what it found and lets `bootstrapSession` do the asking.
 */
import type { InvalidationReason } from './authState';

const STORAGE_KEY = 'mobaide-session';
const CHANNEL_NAME = 'mobaide-session';

/**
 * The readable half of the server's cookie pair. It carries no secret — its
 * presence just says a refresh cookie should exist, which is what lets a
 * never-signed-in visitor skip the restore request entirely.
 *
 * A cookie rather than a `localStorage` flag on purpose: Safari's ITP caps
 * script-writable storage at seven days of no interaction but does not touch
 * server-set cookies, so a stored hint would expire before the credential and
 * quietly cut the 30-day session to seven. Written and deleted in the same
 * response as the credential, the two cannot skew.
 *
 * Both names, because which one the server emits depends on its *runtime*
 * `USER_SESSION_COOKIE_SECURE` — a build-time gate here would look for a name
 * the server never set and silently restore the very bug this design fixes.
 *
 * Trust differs from presence, though. `__Host-` forbids a `Domain`, so only
 * this origin can have written the prefixed name — provided the reader matches
 * the browser's own name normalization, which is what `readSignedInMarker`
 * below is careful about. The bare name is plantable by any sibling host on
 * the registrable domain. A planted marker cannot authenticate anything (the
 * server reads exactly one credential name), but it can drive this tab into a
 * restore that 401s, and a `terminal` verdict purges recents that cannot be
 * re-minted. So both names are believed enough to *attempt* a restore, and
 * only the prefixed one is believed enough to *destroy* anything.
 */
const SIGNED_IN_COOKIE_TRUSTED = '__Host-mobaide_signed_in';
const SIGNED_IN_COOKIE_PLAIN = 'mobaide_signed_in';

export type SignedInMarker = 'trusted' | 'untrusted' | 'absent';

/**
 * How long a booting tab waits for a peer to answer its handover request.
 * Long enough for a peer's message loop to turn over, short enough that a
 * genuinely first tab is not held on a splash screen.
 */
const HANDOFF_TIMEOUT_MS = 300;

export interface UserSession {
  accessToken: string;
  userId: string;
  email: string;
  role: string;
  scheduledDeletionAt: string | null;
}

/**
 * What a booting tab found locally, and therefore what the caller may do next.
 *
 * `restorable` is not "no session" — it is "ask the server". Anything that
 * cannot answer the question (a thrown `document.cookie`, a missing
 * `BroadcastChannel`) reports `restorable` rather than `none`: attempting a
 * restore that fails costs one request, while skipping one that would have
 * succeeded signs a valid user out, which is the bug this all exists to fix.
 */
export type HydrationResult =
  | { kind: 'session'; session: UserSession }
  | { kind: 'restorable'; marker: Exclude<SignedInMarker, 'absent'> }
  | { kind: 'none' };

type ChannelMessage =
  | { type: 'request' }
  | { type: 'offer'; session: UserSession }
  | { type: 'update'; session: UserSession }
  // `userId` carries the same rule the `update` handler already enforces: a
  // sign-out belongs to one account, and a stale tab must not tear down a tab
  // that has since signed in as someone else.
  | { type: 'logout'; reason: InvalidationReason; userId: string | null };

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
    return isSession(value) ? project(value) : null;
  } catch {
    return null;
  }
}

/**
 * Rebuild from the known fields rather than adopting the parsed object.
 *
 * A blob written before the refresh token moved into a cookie still carries
 * one; projecting drops it on the first read instead of leaving a live 30-day
 * credential sitting in storage for the tab's lifetime. Bumping the storage
 * key would do the same, at the cost of signing out every open tab on deploy.
 */
function project(session: UserSession): UserSession {
  return {
    accessToken: session.accessToken,
    userId: session.userId,
    email: session.email,
    role: session.role,
    scheduledDeletionAt: session.scheduledDeletionAt,
  };
}

function isSession(value: unknown): value is UserSession {
  if (typeof value !== 'object' || value === null) return false;
  const s = value as Record<string, unknown>;
  return (
    typeof s.accessToken === 'string' &&
    s.accessToken.length > 0 &&
    typeof s.userId === 'string' &&
    typeof s.email === 'string' &&
    typeof s.role === 'string' &&
    (typeof s.scheduledDeletionAt === 'string' || s.scheduledDeletionAt === null)
  );
}

/**
 * Whether the server says a refresh cookie should exist.
 *
 * An unreadable `document.cookie` (a sandboxed iframe without
 * `allow-same-origin`) answers true: fail-open, because a false negative here
 * signs out a user who is genuinely signed in.
 */
function readSignedInMarker(): SignedInMarker {
  let jar: string;
  try {
    jar = document.cookie;
  } catch {
    // Unreadable jar (a sandboxed iframe without `allow-same-origin`). Attempt
    // the restore — a false negative signs out a user who is genuinely signed
    // in — but do not license a purge on something we could not read.
    return 'untrusted';
  }
  // Parse the way a browser does, not the way `trim()` does. `trim()` strips
  // every Unicode space, so a U+2000-padded `__Host-mobaide_signed_in` —
  // which a browser stores verbatim, prefix rules never firing because the
  // name does not literally begin with `__Host-` — would normalize back into
  // the trusted name here. A nameless `Set-Cookie: __Host-mobaide_signed_in`
  // is the same trick from the other side: it round-trips as a bare token, so
  // splitting on `=` and taking [0] would read the value as a name. Both are
  // settable by any sibling host on the registrable domain, which is exactly
  // the adversary this trust split exists to exclude.
  const names = jar.split(';').flatMap((entry) => {
    const eq = entry.indexOf('=');
    if (eq === -1) return [];
    return [entry.slice(0, eq).replace(/^[ \t]+|[ \t]+$/g, '')];
  });
  if (names.includes(SIGNED_IN_COOKIE_TRUSTED)) return 'trusted';
  return names.includes(SIGNED_IN_COOKIE_PLAIN) ? 'untrusted' : 'absent';
}

/**
 * Drop the readable marker so the next boot skips the restore leg.
 *
 * Sign-out must be effective locally whether or not the server hears about it.
 * `postLogout()` is fire-and-forget over a network that may be gone, and if it
 * never lands both cookies survive — so without this a signed-out browser
 * silently signs itself back in on the next visit, which on a shared machine
 * is the worst possible moment to get it wrong. The credential itself is
 * HttpOnly and can only be revoked server-side; killing the marker is what
 * this page is able to do unilaterally.
 */
export function clearSignedInMarker(): void {
  for (const name of [SIGNED_IN_COOKIE_TRUSTED, SIGNED_IN_COOKIE_PLAIN]) {
    try {
      document.cookie = `${name}=; Max-Age=0; Path=/; SameSite=Strict; Secure`;
      document.cookie = `${name}=; Max-Age=0; Path=/; SameSite=Strict`;
    } catch {
      // Storage denied: the server-side revoke is the only remaining lever.
    }
  }
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
    if (message.type === 'logout') {
      // A tab still running the previous build posts no `userId` at all, and
      // the store's mismatch guard would read `undefined` as "a different
      // account" and ignore the sign-out entirely — leaving a new-build tab
      // authenticated after the user signed out next door. `null` is the
      // legacy meaning: applies to whoever is here.
      remoteHandler?.({
        type: 'logout',
        reason: message.reason,
        userId: typeof message.userId === 'string' ? message.userId : null,
      });
      return;
    }
    if (message.type === 'update') {
      // Normalize at the boundary, like `parseSession` and the `offer` path.
      // A tab still running the previous build broadcasts a session that
      // carries a refresh token, and adopting it verbatim would write that
      // 30-day credential straight back into the storage this design exists
      // to empty — for the whole deploy window.
      if (!isSession(message.session)) return;
      remoteHandler?.({ type: 'update', session: project(message.session) });
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
  const userId = current?.userId ?? null;
  current = null;
  persist(null);
  if (broadcast) post({ type: 'logout', reason, userId });
}

/** Ask open tabs to hand their session over, resolving null on silence. */
export function requestPeerSession(
  timeoutMs = HANDOFF_TIMEOUT_MS,
): Promise<UserSession | null> {
  const ch = getChannel();
  if (ch === null) return Promise.resolve(null);
  return new Promise<UserSession | null>((resolve) => {
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
        finish(project(message.session));
      }
    };
    const timer = setTimeout(() => {
      finish(null);
    }, timeoutMs);
    ch.addEventListener('message', onMessage);
    post({ type: 'request' });
  });
}

/**
 * Resolve this tab's session at boot: own storage, then a peer handover.
 *
 * Async by necessity, which is why the session store exposes a `hydrated` flag
 * and the app holds a splash until this settles — rendering the login screen
 * first would flash it for every new tab of a signed-in user.
 *
 * Returning `restorable` rather than a session is what keeps the network out
 * of this module.
 *
 * The marker gates only the network leg, deliberately *after* the handover
 * rather than before it. A live peer is ground truth; the marker is an
 * inference about the cookie jar, and the two come apart exactly when cookies
 * are blocked: sign-in still works and still fills `sessionStorage`, so a peer
 * can hold a real session while no marker exists anywhere. Checking the marker
 * first would refuse that handover and send a signed-in user back to the login
 * screen — the very bug this all exists to fix, reintroduced in a narrower
 * case. The cost of that ordering is one 300 ms wait for a first-time visitor,
 * which is what they already pay today.
 */
export async function hydrateSession(): Promise<HydrationResult> {
  let raw: string | null = null;
  try {
    raw = sessionStorage.getItem(STORAGE_KEY);
  } catch {
    raw = null;
  }
  const own = parseSession(raw);
  if (own !== null) {
    current = own;
    return { kind: 'session', session: own };
  }

  const offered = await requestPeerSession();
  if (offered !== null) {
    current = offered;
    persist(offered);
    return { kind: 'session', session: offered };
  }

  const marker = readSignedInMarker();
  return marker === 'absent' ? { kind: 'none' } : { kind: 'restorable', marker };
}

/** Test seam — drops in-process state and tears the channel down. */
export function resetSessionTransport(): void {
  current = null;
  remoteHandler = null;
  channel?.close();
  channel = null;
}

/**
 * Rotation of the dashboard/user access token for a session this tab holds.
 *
 * The browser holds no refresh token — it rides an HttpOnly cookie the page
 * cannot read — so the request itself lives in `userSessionApi`, which the
 * session store can import without a cycle. What stays here is everything that
 * depends on the store: the guard that keeps a rotation from resurrecting a
 * signed-out session, and the one that keeps a lost race from signing every
 * tab out.
 *
 * Native clients keep the old shape: they never opt into the cookie transport,
 * so their token stays in the body and a 401 is terminal, which is exactly
 * what Android's `CentralAuthInterceptor` and iOS's `SessionInvalidatorAdapter`
 * do today.
 */
import { jwtClaims } from './jwtClaims';
import { readSession } from './sessionTransport';
import { singleFlight, USER_REFRESH_KEY } from './singleFlight';
import { useSessionStore } from './sessionStore';
import {
  postRefresh,
  toSession,
  REFRESH_TIMEOUT_MS,
  type RefreshOutcome,
  type RefreshResponseBody,
} from './userSessionApi';

export type { RefreshOutcome } from './userSessionApi';

/** Refresh a little before expiry, matching the mobile clients' 60s leeway. */
const EXPIRY_LEEWAY_SECONDS = 60;

/**
 * Seconds-until-expiry from a JWT's `exp`, or null when it has none.
 *
 * A token with no `exp` never expires — the server omits the claim entirely
 * when the TTL is 0, a local-development opt-out. An unparseable token is
 * reported as expired so the caller refreshes rather than sending garbage.
 */
export function expiresInSeconds(token: string): number | null {
  const claims = jwtClaims(token);
  if (claims === null) return 0;
  const exp = claims.exp;
  if (typeof exp !== 'number') return null;
  return exp - Math.floor(Date.now() / 1000);
}

export function isAccessTokenExpiringSoon(token: string): boolean {
  const remaining = expiresInSeconds(token);
  return remaining !== null && remaining < EXPIRY_LEEWAY_SECONDS;
}

/**
 * Rotate the access token of a session this tab already holds.
 *
 * Shares the `user-refresh` single-flight key — and therefore the cross-tab
 * Web Lock — with `restoreSessionFromCookie`, which is what keeps concurrent
 * tabs from spending the same cookie value. Both return `RefreshOutcome`,
 * which they must, since `singleFlight` hands a queued caller whatever promise
 * is parked under the key.
 */
export function refreshUserSession(): Promise<RefreshOutcome> {
  // One budget covering the wait for the lock and the request together. Every
  // relay request funnels through here when its token is inside the expiry
  // leeway, so an unbounded wait would let one slow tab stall the foreground.
  const deadline = AbortSignal.timeout(REFRESH_TIMEOUT_MS);
  return singleFlight<RefreshOutcome>(
    USER_REFRESH_KEY,
    async () => {
      const session = readSession();
      // The outbound half of "never resurrect a signed-out session". Without
      // it a rotation queued behind the lock would spend the cookie *after* a
      // sign-out and land a fresh 30-day credential in the jar, which the next
      // boot would silently restore. `applyRotatedSession` below rejects the
      // result, but by then the server has already issued it.
      if (session === null) {
        return { kind: 'terminal' };
      }
      let response: Response;
      try {
        response = await postRefresh(deadline);
      } catch {
        // Covers the abort as well — a timeout is transient, so the session
        // stays and the next attempt can succeed.
        return { kind: 'transient' };
      }
      if (response.status === 401) {
        // A peer tab may have rotated while this one was queued on the
        // cross-tab lock, consuming the cookie value this request carried.
        // (Rare now: the jar holds one shared value, so a queued tab normally
        // sends the winner's fresh token and gets a 200. It survives for
        // contexts without `navigator.locks`, where tabs genuinely race.)
        // Awaiting `fetch` drains the pending broadcast, so a *different*
        // access token by now means the peer won — and it must be usable, or
        // "different" would just mean "also stale". Reporting terminal here
        // would sign out every tab including the one that just succeeded.
        const current = readSession();
        if (
          current !== null &&
          current.accessToken !== session.accessToken &&
          !isAccessTokenExpiringSoon(current.accessToken)
        ) {
          return { kind: 'success', session: current };
        }
        return { kind: 'terminal' };
      }
      if (!response.ok) {
        return { kind: 'transient' };
      }
      let body: RefreshResponseBody;
      try {
        body = (await response.json()) as RefreshResponseBody;
      } catch {
        return { kind: 'transient' };
      }
      const next = toSession(body, session);
      if (next === null) {
        return { kind: 'transient' };
      }
      // Through the store guard, never straight to storage: if the user signed
      // out while this was in flight, adopting the rotated token here would
      // re-arm the credential locally and — via writeSession's broadcast —
      // sign every other tab back in.
      if (!useSessionStore.getState().applyRotatedSession(next)) {
        return { kind: 'terminal' };
      }
      return { kind: 'success', session: next };
    },
    {
      // Runs while holding the cross-tab lock. If another tab rotated first,
      // its `update` broadcast has already landed here, so the token in
      // storage is fresh and this tab must not spend the (now consumed)
      // cookie it started with.
      recheck: () => {
        const session = readSession();
        if (session === null) return null;
        return isAccessTokenExpiringSoon(session.accessToken)
          ? null
          : { kind: 'success', session };
      },
      signal: deadline,
    },
    // An abort while queued is `transient`, not a dead credential: the caller
    // keeps its still-valid token and retries rather than signing out.
  ).catch(() => ({ kind: 'transient' }));
}

/**
 * Refresh only if the current token is close to expiry.
 *
 * The null-session early return is deliberate and must stay: it is what keeps
 * every relay request and WebSocket URL builder from attempting a cookie
 * restore. "The cookie is always there, why not just try" would turn a signed-
 * out tab into one that silently signs itself back in on any background poll.
 * Restoring is a boot-time decision, taken once, by `bootstrapSession`.
 */
export async function ensureFreshUserToken(): Promise<RefreshOutcome> {
  const session = readSession();
  if (session === null) return { kind: 'terminal' };
  if (!isAccessTokenExpiringSoon(session.accessToken)) {
    return { kind: 'success', session };
  }
  return refreshUserSession();
}

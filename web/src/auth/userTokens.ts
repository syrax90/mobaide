/**
 * Refresh of the dashboard/user access token.
 *
 * Only sessions minted with `refreshable: true` carry a refresh token; a
 * session without one (any client that did not opt in) treats a 401 as
 * terminal, which is exactly what Android's `CentralAuthInterceptor` and iOS's
 * `SessionInvalidatorAdapter` do today.
 */
import { readSession, type UserSession } from './sessionTransport';
import { singleFlight } from './singleFlight';
import { useSessionStore } from './sessionStore';

/**
 * Outcome vocabulary shared with the agent axis, and with both mobile clients:
 * a 401 is terminal (the credential is dead, sign out), anything else is
 * transient (keep the tokens — a flaky network must not sign the user out).
 */
export type RefreshOutcome =
  | { kind: 'success'; session: UserSession }
  | { kind: 'terminal' }
  | { kind: 'transient' };

/** Refresh a little before expiry, matching the mobile clients' 60s leeway. */
const EXPIRY_LEEWAY_SECONDS = 60;

/** Deadline for a token round trip held under the cross-tab lock. */
export const REFRESH_TIMEOUT_MS = 15_000;

/**
 * Seconds-until-expiry from a JWT's `exp`, or null when it has none.
 *
 * A token with no `exp` never expires — the server omits the claim entirely
 * when the TTL is 0, which is the default for non-refreshable sessions. An
 * unparseable token is reported as expired so the caller refreshes rather than
 * sending garbage.
 */
export function expiresInSeconds(token: string): number | null {
  const payload = token.split('.')[1];
  if (payload === undefined) return 0;
  try {
    const json = atob(payload.replace(/-/g, '+').replace(/_/g, '/'));
    const claims: unknown = JSON.parse(json);
    if (typeof claims !== 'object' || claims === null) return 0;
    const exp = (claims as Record<string, unknown>).exp;
    if (typeof exp !== 'number') return null;
    return exp - Math.floor(Date.now() / 1000);
  } catch {
    return 0;
  }
}

export function isAccessTokenExpiringSoon(token: string): boolean {
  const remaining = expiresInSeconds(token);
  return remaining !== null && remaining < EXPIRY_LEEWAY_SECONDS;
}

interface RefreshResponseBody {
  access_token?: unknown;
  refresh_token?: unknown;
  user_id?: unknown;
  email?: unknown;
  role?: unknown;
  scheduled_deletion_at?: unknown;
}

/**
 * Rotate the user session.
 *
 * Uses the bare `fetch` rather than `relayClient` on purpose: the client's
 * bearer middleware would attach the very token being replaced, and its 401
 * handler would sign the user out on the response this function needs to
 * interpret itself. Android solves the same cycle with a dedicated
 * interceptor-free OkHttp client.
 */
export function refreshUserSession(): Promise<RefreshOutcome> {
  return singleFlight<RefreshOutcome>(
    'user-refresh',
    async () => {
      const session = readSession();
      if (session === null || session.refreshToken === null) {
        return { kind: 'terminal' };
      }
      let response: Response;
      try {
        response = await fetch('/api/auth/refresh', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: session.refreshToken }),
          // Without a deadline a stalled connection holds the cross-tab Web
          // Lock until the OS gives up, blocking every other tab's refresh.
          // OkHttp and URLSession give the mobile clients this for free.
          signal: AbortSignal.timeout(REFRESH_TIMEOUT_MS),
        });
      } catch {
        // Covers the abort as well — a timeout is transient, so the tokens
        // stay and the next attempt can succeed.
        return { kind: 'transient' };
      }
      if (response.status === 401) {
        // A peer tab may have rotated while this one was queued on the
        // cross-tab lock. `recheck` cannot see that reliably — it reads the
        // in-memory session, which only advances when the peer's broadcast is
        // delivered, and message delivery has no ordering guarantee against
        // the lock handoff. So the token we just spent may simply have been
        // consumed by the winner. Awaiting `fetch` drains the pending message,
        // so by now the session holds the peer's token: a different refresh
        // token means this 401 was a lost race, not a dead session. Reporting
        // terminal here would sign out every tab, including the one that just
        // refreshed successfully.
        const current = readSession();
        if (
          current !== null &&
          current.refreshToken !== null &&
          current.refreshToken !== session.refreshToken
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
      if (typeof body.access_token !== 'string') {
        return { kind: 'transient' };
      }
      const next: UserSession = {
        accessToken: body.access_token,
        refreshToken:
          typeof body.refresh_token === 'string' ? body.refresh_token : null,
        userId: typeof body.user_id === 'string' ? body.user_id : session.userId,
        email: typeof body.email === 'string' ? body.email : session.email,
        role: typeof body.role === 'string' ? body.role : session.role,
        scheduledDeletionAt:
          typeof body.scheduled_deletion_at === 'string'
            ? body.scheduled_deletion_at
            : null,
      };
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
      // refresh token it started with.
      recheck: () => {
        const session = readSession();
        if (session === null) return null;
        return isAccessTokenExpiringSoon(session.accessToken)
          ? null
          : { kind: 'success', session };
      },
    },
  );
}

/** Refresh only if the current token is close to expiry. */
export async function ensureFreshUserToken(): Promise<RefreshOutcome> {
  const session = readSession();
  if (session === null) return { kind: 'terminal' };
  if (!isAccessTokenExpiringSoon(session.accessToken)) {
    return { kind: 'success', session };
  }
  return refreshUserSession();
}

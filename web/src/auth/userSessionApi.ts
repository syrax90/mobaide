/**
 * The three cookie-credentialed calls of the user session: refresh, boot
 * restore, sign-out.
 *
 * Split from `userTokens` for one reason — nothing here may import the session
 * store. `userTokens.refreshUserSession` must (it applies its result through
 * the store's rotation guard), so putting the boot restore and the logout
 * beside it would make `sessionStore → userTokens → sessionStore` a cycle.
 * These two are exactly the calls the store itself has to make, and neither
 * touches store state: the restore hands its session back for
 * `bootstrapSession` to reconcile, and the logout is fire-and-forget.
 *
 * The browser holds no refresh token. It rides an HttpOnly cookie the page
 * cannot read, so `credentials: 'same-origin'` — the fetch default, spelled
 * out because it is now load-bearing — is what authenticates every call below.
 */
import { lockNameFor, singleFlight, USER_REFRESH_KEY } from './singleFlight';
import { clearSignedInMarker, type UserSession } from './sessionTransport';

/**
 * Proof to the server that this request came from our own page rather than a
 * cross-site one. `SameSite=Strict` cannot carry that alone: it is scoped to
 * the registrable domain, so a page on a sibling host (get.mobaide.com, which
 * is GitHub Pages) is same-site and its forged POST would arrive with the
 * cookie attached. A custom header makes the request non-simple, forcing a
 * preflight that dies on the deliberately absent CORS middleware.
 */
const ORIGIN_PROOF_HEADER = 'X-Mobaide-Session';

/**
 * Outcome vocabulary shared with the agent axis, and with both mobile clients:
 * a 401 is terminal (the credential is dead, sign out), anything else is
 * transient (keep the tokens — a flaky network must not sign the user out).
 */
export type RefreshOutcome =
  | { kind: 'success'; session: UserSession }
  | { kind: 'terminal' }
  | { kind: 'transient' };

/** Deadline for a token round trip held under the cross-tab lock. */
export const REFRESH_TIMEOUT_MS = 15_000;

/**
 * Tighter deadline for the boot restore, which holds the cross-tab lock *and*
 * the splash screen. A timeout is transient, so nothing is destroyed — the
 * user just reaches the login screen instead of watching `Loading…` for the
 * full refresh budget.
 */
export const RESTORE_TIMEOUT_MS = 5_000;

/**
 * Deadline for sign-out. Short because it holds the same cross-tab lock every
 * other tab's refresh queues behind, and because the local teardown has
 * already happened — this call is the server half catching up.
 */
export const LOGOUT_TIMEOUT_MS = 5_000;

export interface RefreshResponseBody {
  access_token?: unknown;
  user_id?: unknown;
  email?: unknown;
  role?: unknown;
  scheduled_deletion_at?: unknown;
}

/**
 * Post to the refresh endpoint.
 *
 * A bare `fetch` rather than `relayClient` on purpose: the client's bearer
 * middleware would attach the very token being replaced, and its 401 handler
 * would sign the user out on the response the caller needs to interpret
 * itself. Android solves the same cycle with a dedicated interceptor-free
 * OkHttp client. No body — the cookie is the credential.
 */
export function postRefresh(signal: AbortSignal): Promise<Response> {
  return fetch('/api/auth/refresh', {
    method: 'POST',
    headers: { [ORIGIN_PROOF_HEADER]: '1' },
    credentials: 'same-origin',
    // Never omit this. A stalled connection holds the cross-tab Web Lock until
    // the OS gives up — minutes — and every other tab's refresh queues behind
    // it. OkHttp and URLSession give the mobile clients the same for free.
    signal,
  });
}

/**
 * Build a session from a refresh response, falling back to the live one.
 *
 * At boot there is no fallback, so a body missing any identity field is
 * unusable rather than half-adopted.
 */
export function toSession(
  body: RefreshResponseBody,
  fallback: UserSession | null,
): UserSession | null {
  if (typeof body.access_token !== 'string') return null;
  const userId = typeof body.user_id === 'string' ? body.user_id : fallback?.userId;
  const email = typeof body.email === 'string' ? body.email : fallback?.email;
  const role = typeof body.role === 'string' ? body.role : fallback?.role;
  if (userId === undefined || email === undefined || role === undefined) return null;
  return {
    accessToken: body.access_token,
    userId,
    email,
    role,
    scheduledDeletionAt:
      typeof body.scheduled_deletion_at === 'string'
        ? body.scheduled_deletion_at
        : null,
  };
}

async function readRefreshOutcome(
  response: Response,
  fallback: UserSession | null,
): Promise<RefreshOutcome> {
  if (response.status === 401) return { kind: 'terminal' };
  if (!response.ok) return { kind: 'transient' };
  let body: RefreshResponseBody;
  try {
    body = (await response.json()) as RefreshResponseBody;
  } catch {
    return { kind: 'transient' };
  }
  const session = toSession(body, fallback);
  return session === null ? { kind: 'transient' } : { kind: 'success', session };
}

/**
 * Mint a session from the cookie when this tab holds none — boot only.
 *
 * The one path allowed to promote an unauthenticated tab, which is why it is
 * separate from `refreshUserSession` rather than a flag on it: it skips that
 * function's null-session precondition on the way in and its
 * `applyRotatedSession` call on the way out, returning the session for
 * `bootstrapSession` to reconcile against anything the channel delivered
 * meanwhile.
 *
 * Shares the `user-refresh` single-flight key, and therefore the cross-tab Web
 * Lock, with `refreshUserSession` — which is what turns a mass tab restore
 * from "one winner and N-1 spurious 401s" into N clean serialized rotations.
 * Both must keep returning `RefreshOutcome`, since `singleFlight` hands a
 * queued caller whatever promise is parked under the key.
 *
 * No `recheck`: the value one would look for arrives as an `update` broadcast,
 * and the store drops those in an unauthenticated tab — which every booting
 * tab is — so it could never fire.
 */
export function restoreSessionFromCookie(): Promise<RefreshOutcome> {
  // One budget for the whole boot, covering the wait for the lock as well as
  // the request. A deadline created inside the task would start only once the
  // lock was granted, leaving the splash hostage to however many tabs are
  // queued ahead. `transient` is the safe landing: it touches nothing on disk,
  // so a contended boot shows the login screen with recents intact rather than
  // spinning.
  const deadline = AbortSignal.timeout(RESTORE_TIMEOUT_MS);
  return singleFlight<RefreshOutcome>(
    USER_REFRESH_KEY,
    async () => {
      try {
        return await readRefreshOutcome(await postRefresh(deadline), null);
      } catch {
        return { kind: 'transient' };
      }
    },
    { signal: deadline },
  ).catch(() => ({ kind: 'transient' }));
}

/**
 * Revoke this browser's refresh cookie server-side.
 *
 * Fire-and-forget: sign-out is a synchronous local teardown and must not wait
 * on the network. `keepalive` lets the request outlive a tab that closes on
 * its heels, and the rejection is swallowed because the account-deletion path
 * signs out against a server that has just deleted the user.
 *
 * Takes the refresh lock first so it cannot interleave with a rotation in this
 * or any other tab: a rotation's `Set-Cookie` landing after the logout's
 * deletion would strand a live 30-day credential in the jar. Uses
 * `navigator.locks` directly rather than `singleFlight`, which would collapse
 * this onto a concurrent rotation's promise and never run it.
 *
 * Revoking *every* browser is a different call: it is user-authenticated, so
 * it goes through `relayClient` in `api/authRepository.ts`.
 */
export function postLogout(): void {
  const send = () =>
    fetch('/api/auth/logout', {
      method: 'POST',
      headers: { [ORIGIN_PROOF_HEADER]: '1' },
      credentials: 'same-origin',
      keepalive: true,
      signal: AbortSignal.timeout(LOGOUT_TIMEOUT_MS),
    }).then(
      () => undefined,
      () => undefined,
    );
  if (typeof navigator === 'undefined' || navigator.locks === undefined) {
    void send();
    return;
  }
  // Bound the wait as well as the work, but wait out a *legitimate* holder:
  // a rotation's own budget is REFRESH_TIMEOUT_MS, so a shorter wait here
  // would jump the queue by design rather than only when the lock is wedged.
  // That matters because a rotation landing after the logout re-sets both
  // cookies — re-arming a browser the user just signed out of.
  //
  // If the lock genuinely cannot be had, send unlocked rather than not at all:
  // never sending strands the credential for certain, and `keepalive` cannot
  // rescue a request that sat queued until the tab closed. Re-clear the marker
  // afterwards so that even if a rotation did re-install it, the next boot
  // does not treat it as licence to restore.
  void navigator.locks
    .request(
      lockNameFor(USER_REFRESH_KEY),
      { signal: AbortSignal.timeout(REFRESH_TIMEOUT_MS) },
      send,
    )
    .catch(() =>
      send().then(() => {
        clearSignedInMarker();
      }),
    );
}

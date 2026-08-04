import createClient, { type Middleware } from 'openapi-fetch';
import {
  getAgentToken,
  getUserToken,
  prepareAgentToken,
  refreshUserTokenIfNeeded,
} from './auth';
import type { paths as AgentPaths } from './generated/agent';
import type { paths as CentralServerPaths } from './generated/central-server';

function bearerMiddleware(getToken: () => string | null): Middleware {
  return {
    onRequest({ request }) {
      const token = getToken();
      if (token !== null) {
        request.headers.set('Authorization', `Bearer ${token}`);
      }
      return request;
    },
  };
}

/**
 * Marks a request this middleware already retried, so a 401 on the retry falls
 * through to the teardown instead of looping. The web analogue of Android's
 * `alreadyRefreshed` guard in `AuthInterceptor`.
 */
const RETRY_MARKER = 'X-Mobaide-Retried';

/**
 * Paths that must never trigger a session teardown on 401.
 *
 * These either establish a session or are the recovery path for a dead one, so
 * their 401 means "those credentials were wrong", not "your session died".
 * Signing the user out here would turn a mistyped password into a logout, and
 * would make the refresh endpoint tear down the very session it is renewing.
 * Same list as Android's `AuthInterceptor`.
 *
 * Enumerated rather than matched on an `/api/auth/` prefix: the account
 * endpoints under `/api/auth/me/...` are session-authenticated like any other
 * route, so a 401 from them really does mean the session is gone.
 */
const UNAUTHENTICATED_PATHS = [
  '/api/auth/register',
  '/api/auth/login',
  '/api/auth/google',
  '/api/auth/apple',
  '/api/auth/verify-email',
  '/api/auth/resend-verification',
  '/api/auth/forgot-password',
  '/api/auth/reset-password',
  '/api/auth/refresh',
  '/api/mobile/refresh',
];

type SessionInvalidator = () => void;

let onUserUnauthorized: SessionInvalidator | null = null;

/**
 * Register the teardown that a relay-axis 401 triggers. Wired by
 * `installAuthProviders` to `invalidateUserSession('tokenRejected')`.
 */
export function setUserUnauthorizedHandler(handler: SessionInvalidator | null): void {
  onUserUnauthorized = handler;
}

type ReachabilityReporter = () => void;

let onServerReachable: ReachabilityReporter | null = null;

/**
 * Register the "the server answered" signal, wired by `installAuthProviders`
 * to the realtime reachability tracker.
 *
 * A seam rather than a direct import for the reason stated at the top of this
 * file: `clients.ts` stays free of anything that would drag app state into a
 * request-shape test. It matters because the reachability signal is otherwise
 * driven only by the WebSocket, and a proxy that blocks WS upgrades while
 * passing HTTP would leave TanStack Query pinned offline.
 */
export function setServerReachableHandler(handler: ReachabilityReporter | null): void {
  onServerReachable = handler;
}

const reachabilityMiddleware: Middleware = {
  onResponse({ response }) {
    // Any status at all — a 4xx is still proof the server is there.
    onServerReachable?.();
    return response;
  },
};

function relayPath(url: string): string {
  return new URL(url, 'http://relay.invalid').pathname;
}

/**
 * Keeps the user access token fresh, and recovers from a 401 that slips past
 * the pre-emptive check.
 *
 * A refreshable session's access token expires (15 minutes by default), so
 * without the `onRequest` half the client would simply start 401ing and sign
 * the user out — the refresh token would never be redeemed. `onResponse`
 * covers the cases the expiry check cannot predict: clock skew, a server
 * restart with a rotated JWT secret, or a token that died mid-flight.
 *
 * Both halves skip `UNAUTHENTICATED_PATHS`: those calls carry no session, and
 * refreshing before them would be pointless work on the login screen.
 */
/**
 * A pre-send copy of each in-flight request, so a 401 can be retried after a
 * refresh. `onResponse` receives the same Request instance that was handed to
 * `fetch`, whose body stream is already consumed — the clone has to be taken
 * before the send. Weakly held so an abandoned request is collected normally.
 */
const replayable = new WeakMap<Request, Request>();

const userSessionMiddleware: Middleware = {
  async onRequest({ request }) {
    if (UNAUTHENTICATED_PATHS.includes(relayPath(request.url))) {
      return request;
    }
    await refreshUserTokenIfNeeded();
    replayable.set(request, request.clone());
    return request;
  },

  async onResponse({ request, response }) {
    if (response.status !== 401) return response;
    if (UNAUTHENTICATED_PATHS.includes(relayPath(request.url))) {
      return response;
    }

    const original = replayable.get(request);
    // No clone means this *is* a retry (the retry path never registers one),
    // so the refreshed token was rejected too — stop and tear down rather than
    // loop. The web analogue of Android's `alreadyRefreshed` guard.
    if (original === undefined || request.headers.has(RETRY_MARKER)) {
      onUserUnauthorized?.();
      return response;
    }

    // The credential this 401 actually rejected. `bearerMiddleware` set it on
    // this same instance, so it is exactly what went out.
    const sent = request.headers.get('Authorization');
    const outcome = await refreshSession();
    const token = getUserToken();
    // An unchanged token means nothing was renewed. The refresh reports
    // success without a round trip whenever the token is not near expiry, so
    // this covers every 401 that was never about expiry — an admin
    // unregistering the account, a self-delete, or a clock far enough behind
    // the server. `get_api_user` returns 401 with a perfectly valid JWT in
    // those cases, and it is the signal the whole `accountRemoved` path hangs
    // off. Replaying the same token would 401 again forever, and because the
    // replay bypasses this middleware nothing would ever tear the session
    // down. A `transient` outcome tears down for the same reason: the token
    // was already rejected, so there is nothing left to stay signed in with.
    if (outcome !== 'success' || token === null || `Bearer ${token}` === sent) {
      onUserUnauthorized?.();
      return response;
    }

    original.headers.set('Authorization', `Bearer ${token}`);
    original.headers.set(RETRY_MARKER, '1');
    const retried = await fetch(original);
    // The replay is a bare `fetch`, so it never re-enters this middleware.
    // A 401 on a genuinely rotated token means the session died between the
    // refresh and the replay (account removed, admin unregister) — without
    // this the SPA would keep an authenticated UI over a dead session.
    if (retried.status === 401) {
      onUserUnauthorized?.();
    }
    return retried;
  },
};

type SessionRefresher = () => Promise<'success' | 'terminal' | 'transient'>;

let refreshSession: SessionRefresher = () => Promise.resolve('terminal');

/** Registered by `installAuthProviders` alongside the unauthorized handler. */
export function setSessionRefresher(next: SessionRefresher): void {
  refreshSession = next;
}

/**
 * Relay-axis client: routes served by the central server itself,
 * authenticated with the dashboard/user JWT. The SPA is same-origin with the
 * relay (Vite dev proxy locally, Caddy in production), so the base URL is
 * the page origin.
 */
export const relayClient = createClient<CentralServerPaths>({ baseUrl: '/' });
// Order is load-bearing: openapi-fetch runs `onRequest` in registration order,
// so the refresh must be registered first for the bearer that follows it to
// read the rotated token. (`onResponse` runs in reverse, which puts the 401
// handler after the bearer — also what we want.)
relayClient.use(userSessionMiddleware);
relayClient.use(bearerMiddleware(getUserToken));
relayClient.use(reachabilityMiddleware);

/**
 * Agent-axis client: the relay's `/proxy/{agentId}/...` catch-all strips the
 * prefix and forwards the remaining path over the tunnel, so agent-schema
 * paths resolve beneath it. The catch-all is untyped in the central-server
 * schema — the agent schema is the source of truth for the proxied surface.
 * This axis requires the per-agent `mobile_access` JWT, not the user JWT
 * (the relay's `get_mobile_user` guard rejects any other token type).
 */
export function createAgentClient(agentId: string) {
  const client = createClient<AgentPaths>({
    baseUrl: `/proxy/${encodeURIComponent(agentId)}`,
  });
  // Registered before the bearer so the token exists by the time it is read.
  // `ensureAgentToken` is single-flight per agent and short-circuits to the
  // cached value unless it is near expiry, so this costs a round trip only on
  // the first call for an agent — which is exactly the call that would
  // otherwise go out with no Authorization header at all and 401.
  client.use({
    async onRequest({ request }) {
      await prepareAgentToken(agentId);
      return request;
    },
  });
  client.use(bearerMiddleware(() => getAgentToken(agentId)));
  client.use(reachabilityMiddleware);
  // Deliberately no unauthorizedMiddleware: a 401 here means this agent's
  // token is stale, not that the user session died. Re-minting is the agent
  // axis's own recovery (see ensureAgentToken), and tearing down the user
  // session for it would sign the user out over one unreachable agent.
  return client;
}

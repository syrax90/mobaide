/**
 * Bind the API layer's token seams to the live session.
 *
 * Kept separate from both sides so `src/api/clients.ts` stays free of any
 * dependency on session state — otherwise the import graph would cycle
 * (clients -> auth -> clients) and every client test would pull in storage.
 * Called once from `main.tsx` before the app renders.
 */
import {
  setAgentTokenProvider,
  setUserTokenProvider,
  setUserTokenRefresher,
} from '../api/auth';
import {
  setServerReachableHandler,
  setSessionRefresher,
  setUserUnauthorizedHandler,
} from '../api/clients';
import { queryClient } from '../lib/queryClient';
import { reportRestReachable } from '../realtime/reachability';
import { getAgentToken } from './agentTokens';
import { getAccessToken } from './sessionTransport';
import { useSessionStore } from './sessionStore';
import { ensureFreshUserToken, refreshUserSession } from './userTokens';

export function installAuthProviders(): void {
  setUserTokenProvider(getAccessToken);
  setAgentTokenProvider(getAgentToken);
  // Pre-emptive: a refreshable session's access token expires (15 minutes by
  // default), so without this the client would simply start 401ing and the
  // refresh token would never be redeemed.
  setUserTokenRefresher(async () => {
    await ensureFreshUserToken();
  });
  // Reactive fallback for a 401 the expiry check could not predict.
  setSessionRefresher(async () => (await refreshUserSession()).kind);
  setUserUnauthorizedHandler(() => {
    useSessionStore.getState().invalidateUserSession('tokenRejected');
  });
  // A REST response is independent proof the server is reachable, which keeps a
  // blocked WebSocket upgrade from pinning TanStack Query offline.
  setServerReachableHandler(reportRestReachable);

  // Drop every cached query whenever the account changes.
  //
  // Subscribing to the store rather than hanging this off one call site is
  // what makes it exhaustive: sign-out, post-delete sign-out, a rejected
  // token, and a peer tab's `logout` broadcast all end a session, and they do
  // not share a single entry point. Query keys are account-agnostic (the
  // devices list is just `['devices']`) and the default staleTime is 30s, so
  // a second account signing in soon after the first signed out would
  // otherwise be served the previous user's data straight from cache, with no
  // request made under the new session.
  //
  // removeQueries, not invalidateQueries: invalidation would refetch every
  // active query on the way out, each with a credential that is gone.
  let previousUserId = useSessionStore.getState().session?.userId ?? null;
  useSessionStore.subscribe((state) => {
    const userId = state.session?.userId ?? null;
    if (userId === previousUserId) return;
    const ended = previousUserId !== null;
    previousUserId = userId;
    if (ended) queryClient.removeQueries();
  });
}

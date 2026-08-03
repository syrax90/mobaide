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
import { setSessionRefresher, setUserUnauthorizedHandler } from '../api/clients';
import { queryClient } from '../lib/queryClient';
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
    // removeQueries, not invalidateQueries: invalidation would refetch every
    // active query on the way out, each with a token the server just refused.
    queryClient.removeQueries();
  });
}

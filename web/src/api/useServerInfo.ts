import { useQuery } from '@tanstack/react-query';
import { queryKeys } from '../lib/queryKeys';
import { fetchServerInfo, type ServerInfo } from './bootstrap';

/**
 * The anonymous `/api/version` probe, shared by the version gate and the
 * Google button.
 *
 * `staleTime: Infinity` because the answer only changes when the server is
 * redeployed. What catches a redeploy landing mid-session is the user-events
 * channel reconnecting — `refreshOnUserEventsConnect` invalidates this key,
 * and a redeploy necessarily drops the socket. Not close code 4002: no server
 * path sends it to a user socket (it is the relay↔agent handshake only), so
 * the 4002 handler is parity, not a backstop. A failed probe must not block the
 * app, so callers treat `undefined` as "no gate".
 */
export function useServerInfo() {
  return useQuery<ServerInfo>({
    queryKey: queryKeys.serverInfo(),
    queryFn: fetchServerInfo,
    staleTime: Infinity,
    retry: 1,
  });
}

import { useQuery } from '@tanstack/react-query';
import { queryKeys } from '../lib/queryKeys';
import { fetchServerInfo, type ServerInfo } from './bootstrap';

/**
 * The anonymous `/api/version` probe, shared by the version gate and the
 * Google button.
 *
 * `staleTime: Infinity` because the answer only changes when the server is
 * redeployed — and the WebSocket 4002 close is the backstop that catches an
 * upgrade landing mid-session (WEB-04). A failed probe must not block the app,
 * so callers treat `undefined` as "no gate".
 */
export function useServerInfo() {
  return useQuery<ServerInfo>({
    queryKey: queryKeys.serverInfo(),
    queryFn: fetchServerInfo,
    staleTime: Infinity,
    retry: 1,
  });
}

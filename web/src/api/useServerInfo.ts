import { useQuery } from '@tanstack/react-query';
import { fetchServerInfo, type ServerInfo } from './bootstrap';

export const SERVER_INFO_QUERY_KEY = ['server-info'] as const;

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
    queryKey: SERVER_INFO_QUERY_KEY,
    queryFn: fetchServerInfo,
    staleTime: Infinity,
    retry: 1,
  });
}

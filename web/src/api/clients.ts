import createClient, { type Middleware } from 'openapi-fetch';
import { getAgentToken, getUserToken } from './auth';
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
 * Relay-axis client: routes served by the central server itself,
 * authenticated with the dashboard/user JWT. The SPA is same-origin with the
 * relay (Vite dev proxy locally, Caddy in production), so the base URL is
 * the page origin.
 */
export const relayClient = createClient<CentralServerPaths>({ baseUrl: '/' });
relayClient.use(bearerMiddleware(getUserToken));

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
  client.use(bearerMiddleware(() => getAgentToken(agentId)));
  return client;
}

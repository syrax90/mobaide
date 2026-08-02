import createClient, { type Middleware } from 'openapi-fetch';
import { getAuthToken } from './auth';
import type { paths as AgentPaths } from './generated/agent';
import type { paths as CentralServerPaths } from './generated/central-server';

const authMiddleware: Middleware = {
  onRequest({ request }) {
    const token = getAuthToken();
    if (token !== null) {
      request.headers.set('Authorization', `Bearer ${token}`);
    }
    return request;
  },
};

/**
 * Relay-axis client: routes served by the central server itself. The SPA is
 * same-origin with the relay (Vite dev proxy locally, Caddy in production),
 * so the base URL is the page origin.
 */
export const relayClient = createClient<CentralServerPaths>({ baseUrl: '/' });
relayClient.use(authMiddleware);

/**
 * Agent-axis client: the relay's `/proxy/{agentId}/...` catch-all strips the
 * prefix and forwards the remaining path over the tunnel, so agent-schema
 * paths resolve beneath it. The catch-all is untyped in the central-server
 * schema — the agent schema is the source of truth for the proxied surface.
 */
export function createAgentClient(agentId: string) {
  const client = createClient<AgentPaths>({
    baseUrl: `/proxy/${encodeURIComponent(agentId)}`,
  });
  client.use(authMiddleware);
  return client;
}

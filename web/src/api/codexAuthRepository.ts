import { createAgentClient } from './clients';
import { toApiError } from './errors';

/**
 * Sign Codex in on the agent with an API key.
 *
 * The key is piped to `codex login --with-api-key` over stdin and never
 * persisted by the agent (Codex writes its own `auth.json`). It must not be
 * stored on this side either: no query key, no URL, no store.
 */
export async function codexLogin(agentId: string, apiKey: string): Promise<boolean> {
  const { data, error, response } = await createAgentClient(agentId).POST('/api/codex/login', {
    body: { api_key: apiKey },
  });
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return data.authenticated;
}

import { createAgentClient } from './clients';
import { toApiError } from './errors';
import type { components } from './generated/agent';

export type StartScanRequest = components['schemas']['StartScanRequest'];

/**
 * Start an Auto Scan for a project.
 *
 * The success body is deliberately discarded. The direct-REST handler answers
 * `{"message": "..."}` but the relay tunnel — the only path a browser takes —
 * answers with an `AutoScanInstanceDto` instead (`relay/client.py`), so nothing
 * about it is portable enough to show. The caller authors its own confirmation.
 */
export async function startAutoScan(
  agentId: string,
  request: StartScanRequest,
): Promise<void> {
  const { error, response } = await createAgentClient(agentId).POST('/api/autoscan/start', {
    body: request,
  });
  if (error !== undefined) throw toApiError(response, error);
}

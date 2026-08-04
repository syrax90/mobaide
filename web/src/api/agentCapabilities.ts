import { createAgentClient } from './clients';
import { toApiError } from './errors';
import { agentVersionSchema, type AgentVersionInfo } from '../protocol/dto/agentVersion';

/**
 * Probe which CLIs the agent has installed, and which models each advertises.
 *
 * Both mobile clients re-probe every time the mode picker is entered rather than
 * caching for the wizard's lifetime — installing a CLI while the wizard is open
 * should un-grey its card.
 */
export async function fetchAgentCapabilities(agentId: string): Promise<AgentVersionInfo> {
  const { data, error, response } = await createAgentClient(agentId).GET('/api/version');
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return agentVersionSchema.parse(data);
}

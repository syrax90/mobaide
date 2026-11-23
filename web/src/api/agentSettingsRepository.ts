/**
 * The agent's own settings (`/api/agent/settings`, PROTOCOL.md "keep the
 * computer awake"). Decoded through the Zod mirror in
 * `protocol/dto/agentSettings.ts`, because the same body arrives as the
 * `agent_settings.changed` event.
 */
import { createAgentClient } from './clients';
import { toApiError } from './errors';
import { agentSettingsSchema, type AgentSettings } from '../protocol/dto/agentSettings';

function decode(body: unknown): AgentSettings {
  const decoded = agentSettingsSchema.safeParse(body);
  if (!decoded.success) throw new Error('Unreadable agent settings.');
  return decoded.data;
}

export async function getAgentSettings(agentId: string, signal?: AbortSignal): Promise<AgentSettings> {
  const { data, error, response } = await createAgentClient(agentId).GET(
    '/api/agent/settings',
    signal === undefined ? {} : { signal },
  );
  if (error !== undefined || data === undefined) throw toApiError(response, error);
  return decode(data);
}

/**
 * Keep the agent's computer awake, or let it sleep again, for every client of
 * the agent. The agent applies it before it answers; a failure to apply comes
 * back as `keep_awake.state`, not as an error.
 */
export async function setKeepAwake(agentId: string, enabled: boolean): Promise<AgentSettings> {
  const { data, error, response } = await createAgentClient(agentId).PUT(
    '/api/agent/settings/keep_awake',
    { body: { enabled } },
  );
  if (error !== undefined || data === undefined) throw toApiError(response, error);
  return decode(data);
}

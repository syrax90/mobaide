/**
 * Account-scoped custom agent types — the central-server axis, not the agent.
 *
 * A type is launched by sending `cli: "custom_<id>"` and nothing else: the relay
 * looks the row up against the authenticated owner and injects the stored
 * command itself (`central_server/relay/mobile_proxy.py::_resolve_custom_kind`).
 * The command never travels on a launch request, which is why the agent-direct
 * REST path rejects custom kinds outright.
 */
import { relayClient } from './clients';
import { toApiError } from './errors';
import {
  codePointLength,
  createCustomAgentTypeRequestSchema,
  MAX_TYPE_COMMAND_LENGTH,
  MAX_TYPE_NAME_LENGTH,
} from '../protocol/dto/customAgentTypes';

export { MAX_TYPE_COMMAND_LENGTH, MAX_TYPE_NAME_LENGTH };

export interface CustomAgentType {
  id: string;
  name: string;
  command: string;
  createdAt: string;
  updatedAt: string;
}

/**
 * Pre-flight validation mirroring the server's Pydantic limits, so the common
 * mistakes are caught before a round trip. Messages match both mobile clients.
 *
 * The server counts **code points**, so this must too — `String.length` counts
 * UTF-16 units and would reject a name of emoji the server would accept.
 */
export function validateCustomAgentType(name: string, command: string): string | null {
  const trimmedName = name.trim();
  const trimmedCommand = command.trim();
  if (trimmedName === '') return 'Name is required';
  if (codePointLength(trimmedName) > MAX_TYPE_NAME_LENGTH) {
    return `Name must be at most ${String(MAX_TYPE_NAME_LENGTH)} characters`;
  }
  if (trimmedCommand === '') return 'Command is required';
  if (codePointLength(trimmedCommand) > MAX_TYPE_COMMAND_LENGTH) {
    return `Command must be at most ${String(MAX_TYPE_COMMAND_LENGTH)} characters`;
  }
  return null;
}

export async function listCustomAgentTypes(): Promise<CustomAgentType[]> {
  const { data, error, response } = await relayClient.GET('/api/custom-agent-types');
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return data.map((type) => ({
    id: type.id,
    name: type.name,
    command: type.command,
    createdAt: type.created_at,
    updatedAt: type.updated_at,
  }));
}

export async function createCustomAgentType(input: {
  name: string;
  command: string;
}): Promise<CustomAgentType> {
  const body = createCustomAgentTypeRequestSchema.parse(input);
  const { data, error, response } = await relayClient.POST('/api/custom-agent-types', {
    body,
  });
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return {
    id: data.id,
    name: data.name,
    command: data.command,
    createdAt: data.created_at,
    updatedAt: data.updated_at,
  };
}

/**
 * Delete a type. Deleting also soft-deletes its saved shortcuts server-side, in
 * the same transaction and with no companion `shortcut.deleted` events.
 *
 * A **403** here means the row is already gone or was never ours — the server
 * maps not-found to 403 rather than 404 so a probe cannot enumerate other
 * accounts' ids. Both mobile clients treat it as success, since the user's
 * intent ("make this go away") is satisfied either way.
 */
export async function deleteCustomAgentType(typeId: string): Promise<void> {
  const { error, response } = await relayClient.DELETE('/api/custom-agent-types/{type_id}', {
    params: { path: { type_id: typeId } },
  });
  if (error !== undefined && response.status !== 403) {
    throw toApiError(response, error);
  }
}

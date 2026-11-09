/**
 * Stored chat sessions on the PC, for "Continue a session". The first page can
 * take a few seconds: the agent starts the chat agent process to ask it.
 */
import { createAgentClient } from './clients';
import { toApiError } from './errors';
import {
  chatStoredSessionsResponseSchema,
  type ChatStoredSessionsResponse,
} from '../protocol/dto/chatSessions';

export async function listChatSessions(
  agentId: string,
  kind: string,
  options: { cwd?: string; cursor?: string | null; signal?: AbortSignal } = {},
): Promise<ChatStoredSessionsResponse> {
  const { data, error, response } = await createAgentClient(agentId).GET(
    '/api/chat/{kind}/sessions',
    {
      params: {
        path: { kind },
        query: {
          ...(options.cwd === undefined ? {} : { cwd: options.cwd }),
          ...(options.cursor === undefined || options.cursor === null
            ? {}
            : { cursor: options.cursor }),
        },
      },
      signal: options.signal,
    },
  );
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return chatStoredSessionsResponseSchema.parse(data);
}

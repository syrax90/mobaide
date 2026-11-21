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

/**
 * Delete a stored session on the PC (ACP `session/delete`). Call it only when
 * the list said `can_delete`. The agent answers 409 for a session that is open
 * in a Mobaide chat or in another app on the PC.
 */
export async function deleteChatSession(
  agentId: string,
  kind: string,
  sessionId: string,
): Promise<void> {
  const { error, response } = await createAgentClient(agentId).DELETE(
    '/api/chat/{kind}/sessions/{session_id}',
    { params: { path: { kind, session_id: sessionId } } },
  );
  if (error !== undefined) throw toApiError(response, error);
}

import type { components } from './generated/agent';
import { createAgentClient } from './clients';
import { toApiError } from './errors';

export type FsDirsResponse = components['schemas']['FsDirsResponse'];
export type SessionFileEntry = components['schemas']['SessionFileEntry'];

/** `GET /api/fs/dirs`: subdirectory names of one agent-host directory. */
export async function listDirs(
  agentId: string,
  path: string,
  q: string | null,
  signal?: AbortSignal,
): Promise<FsDirsResponse> {
  const { data, error, response } = await createAgentClient(agentId).GET('/api/fs/dirs', {
    params: { query: q === null ? { path } : { path, q } },
    signal,
  });
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return data;
}

/**
 * `GET /api/sessions/{id}/files`: files and folders of the session's folder
 * that match `q`, ranked by the agent, for the chat composer's `@` suggestions.
 */
export async function listSessionFiles(
  agentId: string,
  sessionId: string,
  q: string,
  signal?: AbortSignal,
): Promise<SessionFileEntry[]> {
  const { data, error, response } = await createAgentClient(agentId).GET(
    '/api/sessions/{session_id}/files',
    { params: { path: { session_id: sessionId }, query: { q, limit: 20 } }, signal },
  );
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return data.entries;
}

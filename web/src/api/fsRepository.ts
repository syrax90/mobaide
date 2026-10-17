import type { components } from './generated/agent';
import { createAgentClient } from './clients';
import { toApiError } from './errors';

export type FsDirsResponse = components['schemas']['FsDirsResponse'];

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

/**
 * The unified branch/worktree list behind a project's Git screen.
 *
 * Two endpoints only — list and batch delete. There is deliberately no branch
 * switch, branch create or worktree create/remove: the agent does not expose
 * them, so neither does any client.
 */
import type { components } from './generated/agent';
import { createAgentClient } from './clients';
import { busyHolders } from './conflicts';
import { toApiError } from './errors';
import type { BusyHolder } from '../protocol/dto/gitConflict';

export type GitItem = components['schemas']['GitItemRead'];
export type GitDeleteItem = components['schemas']['GitDeleteItem'];
export type GitDeleteResult = components['schemas']['GitDeleteResultItem'];

export const KIND_BRANCH_WITH_WORKTREE = 'branch_with_worktree';
export const KIND_BRANCH_ONLY = 'branch_only';
export const KIND_ORPHAN_WORKTREE = 'orphan_worktree';

/**
 * A stable row id, matching Android's `GitItem.id` and iOS's `id` exactly — the
 * delete machine keys its hide-set on this, so a divergence would strand rows.
 */
export function gitItemId(item: Pick<GitItem, 'kind' | 'branch' | 'worktree_path'>): string {
  switch (item.kind) {
    case KIND_BRANCH_WITH_WORKTREE:
    case KIND_BRANCH_ONLY:
      return `${item.kind}:${item.branch ?? item.worktree_path ?? ''}`;
    case KIND_ORPHAN_WORKTREE:
      return `${item.kind}:${item.worktree_path ?? ''}`;
    default:
      return `${String(item.kind)}:${item.branch ?? ''}|${item.worktree_path ?? ''}`;
  }
}

/** The checked-out branch cannot be deleted; everything else can. */
export function isDeletable(item: GitItem): boolean {
  return !item.is_current;
}

/**
 * Raised on the batch-delete 409. The guard runs before any deletion, so
 * **nothing** was processed and every row in the batch must be restored.
 */
export class GitBusyError extends Error {
  readonly holders: BusyHolder[] | null;

  constructor(holders: BusyHolder[] | null) {
    super('Some items are in use.');
    this.name = 'GitBusyError';
    this.holders = holders;
  }
}

export async function listGitItems(
  agentId: string,
  projectName: string,
): Promise<GitItem[]> {
  const { data, error, response } = await createAgentClient(agentId).GET(
    '/api/projects/{name}/git/items',
    { params: { path: { name: projectName } } },
  );
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return data.items;
}

/**
 * Batch delete. Per-item git failures come back in the **200** body so each row
 * can be acted on independently; only the busy guard rejects the whole batch,
 * and it does so before touching anything.
 *
 * The path really does contain a colon (`items:delete`) — an RPC-style verb the
 * agent chose because some proxies drop bodies on `DELETE`. openapi-fetch treats
 * the path as a literal template, so the colon passes through untouched.
 */
export async function deleteGitItems(
  agentId: string,
  projectName: string,
  items: readonly GitDeleteItem[],
): Promise<GitDeleteResult[]> {
  const { data, error, response } = await createAgentClient(agentId).POST(
    '/api/projects/{name}/git/items:delete',
    { params: { path: { name: projectName } }, body: { items: [...items] } },
  );
  if (error !== undefined || data === undefined) {
    const apiError = toApiError(response, error);
    if (apiError.status === 409) throw new GitBusyError(busyHolders(apiError));
    throw apiError;
  }
  return data.results;
}

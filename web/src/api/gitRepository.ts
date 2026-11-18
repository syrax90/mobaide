/**
 * The unified branch/worktree list behind a workspace's Git screen, plus the
 * ref management the screen grew in #677: remote-tracking rows on request,
 * branch creation and a checkout in the workspace root. Worktree creation
 * stays with the session wizard; the agent exposes nothing else here.
 */
import type { components } from './generated/agent';
import { createAgentClient } from './clients';
import { busyHolders } from './conflicts';
import { isApiError, toApiError } from './errors';
import { toSessionGitError } from './sessionGitRepository';
import type { BusyHolder } from '../protocol/dto/gitConflict';

export type GitItem = components['schemas']['GitItemRead'];
export type GitDeleteItem = components['schemas']['GitDeleteItem'];
export type GitDeleteResult = components['schemas']['GitDeleteResultItem'];
export type GitDeleteJob = components['schemas']['GitDeleteJobResponse'];
export type GitBranchCreateRequest = components['schemas']['GitBranchCreateRequest'];
export type GitBranchResult = components['schemas']['GitBranchResponse'];
export type WorktreeBases = components['schemas']['WorktreeBasesResponse'];
export type WorktreeBaseRef = components['schemas']['WorktreeBaseRef'];

export const KIND_BRANCH_WITH_WORKTREE = 'branch_with_worktree';
export const KIND_BRANCH_ONLY = 'branch_only';
export const KIND_ORPHAN_WORKTREE = 'orphan_worktree';
export const KIND_REMOTE_BRANCH = 'remote_branch';

/** The coded refusals of the branch routes (PROTOCOL.md), on top of the session vocabulary. */
export const GIT_CODE_EXISTS = 'exists';
export const GIT_CODE_BAD_BRANCH_NAME = 'bad_branch_name';
export const GIT_CODE_UNKNOWN_START_POINT = 'unknown_start_point';
export const GIT_CODE_DIRTY = 'dirty';
export const GIT_CODE_CHECKED_OUT_ELSEWHERE = 'checked_out_elsewhere';
export const GIT_CODE_OPERATION_IN_PROGRESS = 'operation_in_progress';
export const GIT_CODE_REF_NOT_FOUND = 'ref_not_found';

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

/** A row a delete batch may carry. */
export type DeletableGitItem = GitItem & { kind: GitDeleteItem['kind'] };

/**
 * The checked-out branch cannot be deleted; a remote-tracking row only when the
 * agent advertises `capabilities.git.delete_remote` — an older one answers the
 * whole batch with a 422.
 */
export function isDeletable(item: GitItem, deleteRemote: boolean): item is DeletableGitItem {
  if (item.is_current) return false;
  return item.kind !== KIND_REMOTE_BRANCH || deleteRemote;
}

/**
 * *Remove worktree* keeps the branch: offered on a worktree-backed row other
 * than the workspace root's own checkout.
 */
export function canRemoveWorktree(item: GitItem): boolean {
  return (
    !item.is_current &&
    item.kind === KIND_BRANCH_WITH_WORKTREE &&
    (item.worktree_path ?? '') !== ''
  );
}

/**
 * Branch-list order on all three clients: lowercased names by UTF-16 code
 * unit — deliberately not `localeCompare`, whose collation Kotlin and Swift do
 * not share — with the raw name as tiebreak.
 */
export function compareRefNames(a: string, b: string): number {
  const lowerA = a.toLowerCase();
  const lowerB = b.toLowerCase();
  if (lowerA !== lowerB) return lowerA < lowerB ? -1 : 1;
  if (a === b) return 0;
  return a < b ? -1 : 1;
}

/**
 * Check out is offered for a branch nothing has checked out: a `branch_only`
 * row or a remote-tracking one. The current branch and the worktree-backed
 * rows never get it — git would refuse the latter as checked out elsewhere.
 */
export function canCheckOut(item: GitItem): boolean {
  return !item.is_current && (item.kind === KIND_BRANCH_ONLY || item.kind === KIND_REMOTE_BRANCH);
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

/**
 * Always asks for the remote-tracking rows: the query flag shields *older
 * clients* from a kind they would offer for delete, not this one, and an
 * older agent simply ignores it and answers the local rows.
 */
export async function listGitItems(
  agentId: string,
  workspaceName: string,
): Promise<GitItem[]> {
  const { data, error, response } = await createAgentClient(agentId).GET(
    '/api/projects/{name}/git/items',
    { params: { path: { name: workspaceName }, query: { remote: true } } },
  );
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return data.items;
}

/** How often a running delete job is polled. */
export const DELETE_JOB_POLL_MS = 1000;
/**
 * How long polls may keep failing (agent or relay reconnecting) before the
 * client gives up on a job; the agent keeps a finished job for 10 minutes.
 */
export const DELETE_JOB_GIVE_UP_MS = 120_000;

export interface DeleteGitItemsOptions {
  /**
   * `capabilities.git.delete_jobs`: run the batch as a background job and poll
   * it, so a batch of slow worktree removals never hits the request timeout.
   */
  jobs?: boolean;
  /** Items processed so far, while a job runs. */
  onProgress?: (completed: number, total: number) => void;
}

function toDeleteError(response: Response, error: unknown): Error {
  const apiError = toApiError(response, error);
  if (apiError.status === 409) {
    // Every 409 is the busy guard except a coded refusal (`too_many_jobs`).
    const holders = busyHolders(apiError);
    const detail: unknown = apiError.detail;
    const coded = typeof detail === 'object' && detail !== null && 'code' in detail;
    if (holders !== null || !coded) return new GitBusyError(holders);
  }
  return apiError;
}

const sleep = (ms: number) =>
  new Promise<void>((resolve) => {
    setTimeout(resolve, ms);
  });

async function runDeleteJob(
  agentId: string,
  workspaceName: string,
  items: readonly GitDeleteItem[],
  onProgress: DeleteGitItemsOptions['onProgress'],
): Promise<GitDeleteResult[]> {
  const client = createAgentClient(agentId);
  const started = await client.POST('/api/projects/{name}/git/delete-jobs', {
    params: { path: { name: workspaceName } },
    body: { items: [...items] },
  });
  if (started.error !== undefined || started.data === undefined) {
    throw toDeleteError(started.response, started.error);
  }
  let job: GitDeleteJob = started.data;
  let lastSuccess = Date.now();
  const givenUp = () => Date.now() - lastSuccess >= DELETE_JOB_GIVE_UP_MS;
  while (job.status !== 'done') {
    onProgress?.(job.completed, job.total);
    await sleep(DELETE_JOB_POLL_MS);
    let polled;
    try {
      polled = await client.GET('/api/projects/{name}/git/delete-jobs/{job_id}', {
        params: { path: { name: workspaceName, job_id: job.job_id } },
      });
    } catch (error: unknown) {
      if (givenUp()) throw error;
      continue;
    }
    if (polled.error !== undefined || polled.data === undefined) {
      // A 404 means the agent restarted and forgot the job: nothing will finish it.
      if (polled.response.status === 404 || givenUp()) {
        throw toApiError(polled.response, polled.error);
      }
      continue;
    }
    lastSuccess = Date.now();
    job = polled.data;
  }
  onProgress?.(job.completed, job.total);
  return job.results ?? [];
}

/**
 * Batch delete. Per-item git failures come back with the results so each row
 * can be acted on independently; only the busy guard rejects the whole batch,
 * and it does so before touching anything.
 *
 * With `jobs` the batch runs as an agent background job the client polls;
 * otherwise it is one `items:delete` request, whose path really does contain a
 * colon — an RPC-style verb the agent chose because some proxies drop bodies on
 * `DELETE`. openapi-fetch treats the path as a literal template, so the colon
 * passes through untouched.
 */
export async function deleteGitItems(
  agentId: string,
  workspaceName: string,
  items: readonly GitDeleteItem[],
  options: DeleteGitItemsOptions = {},
): Promise<GitDeleteResult[]> {
  if (options.jobs === true) {
    return runDeleteJob(agentId, workspaceName, items, options.onProgress);
  }
  const { data, error, response } = await createAgentClient(agentId).POST(
    '/api/projects/{name}/git/items:delete',
    { params: { path: { name: workspaceName } }, body: { items: [...items] } },
  );
  if (error !== undefined || data === undefined) {
    throw toDeleteError(response, error);
  }
  return data.results;
}

/**
 * The busy `409` keeps the workspace screen's own class; every other coded
 * detail — the branch refusals and the session vocabulary they reuse —
 * becomes a `SessionGitError`, whose copy the screen already knows.
 */
function toBranchError(error: unknown): unknown {
  if (isApiError(error) && error.status === 409) {
    const holders = busyHolders(error);
    if (holders !== null) return new GitBusyError(holders);
  }
  return toSessionGitError(error);
}

export async function createGitBranch(
  agentId: string,
  workspaceName: string,
  request: GitBranchCreateRequest,
): Promise<GitBranchResult> {
  const { data, error, response } = await createAgentClient(agentId).POST(
    '/api/projects/{name}/git/branches',
    { params: { path: { name: workspaceName } }, body: request },
  );
  if (error !== undefined || data === undefined) {
    throw toBranchError(toApiError(response, error));
  }
  return data;
}

/**
 * `ref` is a row's `branch` verbatim — a local name or `<remote>/<name>`.
 * `force` skips the busy-holder refusal after the user confirmed it.
 */
export async function checkoutGitRef(
  agentId: string,
  workspaceName: string,
  ref: string,
  force = false,
): Promise<GitBranchResult> {
  const { data, error, response } = await createAgentClient(agentId).POST(
    '/api/projects/{name}/git/checkout',
    { params: { path: { name: workspaceName } }, body: { ref, force } },
  );
  if (error !== undefined || data === undefined) {
    throw toBranchError(toApiError(response, error));
  }
  return data;
}

/**
 * The New Session wizard's base-branch list: every local and remote-tracking
 * branch, `default` first, then newest tip first. Local refs only — the agent
 * never fetches for this read.
 */
export async function listWorktreeBases(
  agentId: string,
  workspaceName: string,
): Promise<WorktreeBases> {
  const { data, error, response } = await createAgentClient(agentId).GET(
    '/api/projects/{name}/git/worktree-bases',
    { params: { path: { name: workspaceName } } },
  );
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return data;
}

/**
 * The session-scoped git Changes routes — the agent's working-tree status and
 * per-file diff for the directory a session was spawned in, the
 * stage / unstage / discard / commit mutations of its staged set, the
 * fetch / pull / push jobs with their snapshot and cancel routes, the
 * rebase start / continue / skip / abort routes, and the conflict resolver's
 * read / resolve / take / restore routes.
 *
 * REST-only shapes, so the types come straight from the generated schema like
 * `gitRepository`'s (the `OWNER.generated` policy in
 * `protocol/__fixtures__/coverage.ts`); `GitJobResponse` also rides the agent
 * events channel and has its Zod mirror in `protocol/dto/gitJob.ts`. The one
 * hand-parsed body is the error `detail`, which no schema documents: the
 * git failures carry `{"detail": {"code": …}}` on any status — a `404` with
 * a coded detail is an unknown job, never an old agent — while the two
 * string-detail 404s differ only in FastAPI's text: a session lookup miss
 * answers `Session '<id>' not found`, an agent that predates the routes the
 * relay's `Unknown proxy route` or a plain `Not Found`. Android
 * `SessionGitErrors.kt` and iOS `SessionGitErrors.swift` draw the same lines.
 */
import { createAgentClient } from './clients';
import { isApiError, toApiError, type ApiError } from './errors';
import type { components } from './generated/agent';

export type SessionGitStatus = components['schemas']['GitStatusResponse'];
export type SessionGitEntry = components['schemas']['GitStatusEntry'];
export type SessionGitRepoInfo = components['schemas']['GitRepoInfo'];
export type SessionGitUpstream = components['schemas']['GitUpstream'];
export type SessionGitDiff = components['schemas']['GitDiffResponse'];
export type SessionGitHunk = components['schemas']['GitDiffHunk'];
export type SessionGitLine = components['schemas']['GitDiffLine'];
export type SessionGitDiffScope = SessionGitDiff['scope'];
export type SessionGitDiscardScope = components['schemas']['GitDiscardRequest']['scope'];
export type SessionGitCommitRequest = components['schemas']['GitCommitRequest'];
export type SessionGitCommitResult = components['schemas']['GitCommitResponse'];
export type SessionGitCommitContext = components['schemas']['GitCommitContextResponse'];
export type SessionGitCommitHead = components['schemas']['GitCommitHead'];
export type SessionGitJob = components['schemas']['GitJobResponse'];
export type SessionGitJobOp = SessionGitJob['op'];
export type SessionGitPullMode = NonNullable<components['schemas']['GitPullRequest']['mode']>;
export type SessionGitRebaseInfo = components['schemas']['GitRebaseInfo'];
export type SessionGitRebaseResult = components['schemas']['GitRebaseResponse'];
export type SessionGitConflict = components['schemas']['GitConflictResponse'];
export type SessionGitConflictSide = components['schemas']['GitConflictTakeRequest']['side'];
export type SessionGitConflictStyle = NonNullable<
  components['schemas']['GitConflictRestoreRequest']['style']
>;
/** The three routes that act on a rebase already in progress. */
export type SessionGitRebaseStep = 'continue' | 'skip' | 'abort';

/** What one of the three start routes is asked to do. */
export type SessionGitSyncRequest =
  | { op: 'fetch' }
  | { op: 'pull'; mode: SessionGitPullMode }
  | { op: 'push'; force: boolean };

export const GIT_CODE_UNAVAILABLE = 'git_unavailable';
export const GIT_CODE_TIMEOUT = 'git_timeout';
export const GIT_CODE_ERROR = 'git_error';
export const GIT_CODE_TOO_OLD = 'git_too_old';
/** The `409` refusal vocabulary of the mutating routes (PROTOCOL.md). */
export const GIT_CODE_IDENTITY_REQUIRED = 'identity_required';
export const GIT_CODE_UNMERGED = 'unmerged';
export const GIT_CODE_NOTHING_TO_COMMIT = 'nothing_to_commit';
export const GIT_CODE_NO_HEAD = 'no_head';
export const GIT_CODE_HOOK_FAILED = 'hook_failed';
export const GIT_CODE_INDEX_LOCKED = 'index_locked';
export const GIT_CODE_PATHSPEC_MISMATCH = 'pathspec_mismatch';
export const GIT_CODE_EMPTY_MESSAGE = 'empty_message';
/** Another fetch / pull / push owns the repository; `detailMessage` is its job id. */
export const GIT_CODE_JOB_RUNNING = 'job_running';
/** The `404` a job route answers for an id the agent no longer (or never) knew. */
export const GIT_CODE_JOB_NOT_FOUND = 'job_not_found';
/** The job outcomes a client offers a follow-up for (PROTOCOL.md). */
export const GIT_JOB_CODE_CANCELLED = 'cancelled';
export const GIT_JOB_CODE_AUTH_REQUIRED = 'auth_required';
export const GIT_JOB_CODE_NETWORK = 'network';
export const GIT_JOB_CODE_REMOTE_NOT_FOUND = 'remote_not_found';
export const GIT_JOB_CODE_REJECTED = 'rejected';
export const GIT_JOB_CODE_NOT_FAST_FORWARD = 'not_fast_forward';
export const GIT_JOB_CODE_CONFLICTS = 'conflicts';
export const GIT_JOB_CODE_DIRTY_WORKTREE = 'dirty_worktree';
export const GIT_JOB_CODE_OPERATION_IN_PROGRESS = 'operation_in_progress';
export const GIT_JOB_CODE_NO_UPSTREAM = 'no_upstream';
export const GIT_JOB_CODE_NO_REMOTE = 'no_remote';
export const GIT_JOB_CODE_DETACHED_HEAD = 'detached_head';
/** The rebase routes' refusals; `detailMessage` is the ref, the state or the path. */
export const GIT_CODE_UNKNOWN_REF = 'unknown_ref';
export const GIT_CODE_NO_REBASE = 'no_rebase';
export const GIT_CODE_CONFLICT_MARKERS = 'conflict_markers';
/** The conflict routes' refusals; `detailMessage` is the path. */
export const GIT_CODE_NOT_CONFLICTED = 'not_conflicted';
export const GIT_CODE_FILE_CHANGED = 'file_changed';
export const GIT_CODE_FILE_BUSY = 'file_busy';
export const GIT_CODE_NOT_RESTORABLE = 'not_restorable';
export const GIT_CODE_UNSUPPORTED = 'unsupported';
export const GIT_CODE_TOO_LARGE = 'too_large';
export const GIT_CODE_BAD_CONTENT = 'bad_content';
/** `GitConflictResponse.kind`, an open vocabulary; only `text` is editable. */
export const GIT_CONFLICT_KIND_TEXT = 'text';
/** `GitRebaseResponse.outcome`, an open vocabulary. */
export const GIT_REBASE_REBASED = 'rebased';
export const GIT_REBASE_UP_TO_DATE = 'up_to_date';
export const GIT_REBASE_STOPPED = 'stopped';
export const GIT_REBASE_ABORTED = 'aborted';

export type SessionGitErrorKind = 'agentOutdated' | 'sessionGone' | 'git';

/**
 * A failure of either route, typed so the screens can tell "update your agent"
 * from "session ended" from "git itself refused".
 */
export class SessionGitError extends Error {
  readonly kind: SessionGitErrorKind;
  /** The `detail.code` of a `git` failure; `null` for the two 404s. */
  readonly code: string | null;
  /** The `detail.message` a `git_error` carries. */
  readonly detailMessage: string | null;
  override readonly cause: ApiError;

  constructor(kind: SessionGitErrorKind, cause: ApiError, code: string | null = null) {
    super(cause.message);
    this.name = 'SessionGitError';
    this.kind = kind;
    this.code = code;
    this.cause = cause;
    const detail = cause.detail as { message?: unknown } | null;
    this.detailMessage =
      typeof detail === 'object' && detail !== null && typeof detail.message === 'string'
        ? detail.message
        : null;
  }
}

export function isSessionGitError(error: unknown): error is SessionGitError {
  return error instanceof SessionGitError;
}

/** Neither 404 can change on a retry, so a feed that hits one is finished. */
export function isTerminalSessionGitError(error: unknown): boolean {
  return (
    isSessionGitError(error) && (error.kind === 'agentOutdated' || error.kind === 'sessionGone')
  );
}

const SESSION_NOT_FOUND = /^Session '.*' not found$/;

/** Wrap the failures this surface understands; anything else passes through. */
export function toSessionGitError(error: unknown): unknown {
  if (!isApiError(error)) return error;
  // A coded detail first, whatever the status: the job routes' `404
  // job_not_found` must not read as an old agent, which would end the poll.
  if (typeof error.detail === 'object' && error.detail !== null) {
    const code = (error.detail as { code?: unknown }).code;
    if (typeof code === 'string') return new SessionGitError('git', error, code);
  }
  if (error.status === 404) {
    const detail = typeof error.detail === 'string' ? error.detail : null;
    return new SessionGitError(
      detail !== null && SESSION_NOT_FOUND.test(detail) ? 'sessionGone' : 'agentOutdated',
      error,
    );
  }
  return error;
}

/** The reconcile poll's "nobody knows this job" answer. */
export function isSessionGitJobLost(error: unknown): boolean {
  return isSessionGitError(error) && error.kind === 'git' && error.code === GIT_CODE_JOB_NOT_FOUND;
}

export async function getSessionGitStatus(
  agentId: string,
  sessionId: string,
  etag?: string,
  signal?: AbortSignal,
): Promise<SessionGitStatus> {
  const { data, error, response } = await createAgentClient(agentId).GET(
    '/api/sessions/{session_id}/git/status',
    {
      params: {
        path: { session_id: sessionId },
        ...(etag === undefined ? {} : { query: { etag } }),
      },
      signal,
    },
  );
  if (error !== undefined || data === undefined) {
    throw toSessionGitError(toApiError(response, error));
  }
  return data;
}

export interface SessionGitDiffRequest {
  path: string;
  scope: SessionGitDiffScope;
  context: number;
  /** The rename's source, so a pathspec-limited `git diff -M` can see it. */
  origPath?: string;
}

export async function getSessionGitDiff(
  agentId: string,
  sessionId: string,
  request: SessionGitDiffRequest,
  signal?: AbortSignal,
): Promise<SessionGitDiff> {
  const { data, error, response } = await createAgentClient(agentId).GET(
    '/api/sessions/{session_id}/git/diff',
    {
      params: {
        path: { session_id: sessionId },
        query: {
          path: request.path,
          scope: request.scope,
          // The generated type says string: FastAPI declares the default as
          // `"3"` and coerces on the way in.
          context: String(request.context),
          ...(request.origPath === undefined ? {} : { orig_path: request.origPath }),
        },
      },
      signal,
    },
  );
  if (error !== undefined || data === undefined) {
    throw toSessionGitError(toApiError(response, error));
  }
  return data;
}

function throwSessionGitError(response: Response, error: unknown): never {
  throw toSessionGitError(toApiError(response, error));
}

export async function stageSessionGit(
  agentId: string,
  sessionId: string,
  paths: readonly string[],
): Promise<void> {
  const { error, response } = await createAgentClient(agentId).POST(
    '/api/sessions/{session_id}/git/stage',
    { params: { path: { session_id: sessionId } }, body: { paths: [...paths] } },
  );
  if (error !== undefined) throwSessionGitError(response, error);
}

export async function unstageSessionGit(
  agentId: string,
  sessionId: string,
  paths: readonly string[],
): Promise<void> {
  const { error, response } = await createAgentClient(agentId).POST(
    '/api/sessions/{session_id}/git/unstage',
    { params: { path: { session_id: sessionId } }, body: { paths: [...paths] } },
  );
  if (error !== undefined) throwSessionGitError(response, error);
}

export async function discardSessionGit(
  agentId: string,
  sessionId: string,
  paths: readonly string[],
  scope: SessionGitDiscardScope,
): Promise<void> {
  const { error, response } = await createAgentClient(agentId).POST(
    '/api/sessions/{session_id}/git/discard',
    { params: { path: { session_id: sessionId } }, body: { paths: [...paths], scope } },
  );
  if (error !== undefined) throwSessionGitError(response, error);
}

export async function getSessionGitCommitContext(
  agentId: string,
  sessionId: string,
  signal?: AbortSignal,
): Promise<SessionGitCommitContext> {
  const { data, error, response } = await createAgentClient(agentId).GET(
    '/api/sessions/{session_id}/git/commit/context',
    { params: { path: { session_id: sessionId } }, signal },
  );
  if (error !== undefined || data === undefined) throwSessionGitError(response, error);
  return data;
}

export async function commitSessionGit(
  agentId: string,
  sessionId: string,
  request: SessionGitCommitRequest,
): Promise<SessionGitCommitResult> {
  const { data, error, response } = await createAgentClient(agentId).POST(
    '/api/sessions/{session_id}/git/commit',
    { params: { path: { session_id: sessionId } }, body: request },
  );
  if (error !== undefined || data === undefined) throwSessionGitError(response, error);
  return data;
}

export async function startSessionGitRebase(
  agentId: string,
  sessionId: string,
  onto: string,
): Promise<SessionGitRebaseResult> {
  const { data, error, response } = await createAgentClient(agentId).POST(
    '/api/sessions/{session_id}/git/rebase',
    { params: { path: { session_id: sessionId } }, body: { onto } },
  );
  if (error !== undefined || data === undefined) throwSessionGitError(response, error);
  return data;
}

export async function stepSessionGitRebase(
  agentId: string,
  sessionId: string,
  step: SessionGitRebaseStep,
): Promise<SessionGitRebaseResult> {
  const client = createAgentClient(agentId);
  const params = { path: { session_id: sessionId } };
  const { data, error, response } =
    step === 'continue'
      ? await client.POST('/api/sessions/{session_id}/git/rebase/continue', { params })
      : step === 'skip'
        ? await client.POST('/api/sessions/{session_id}/git/rebase/skip', { params })
        : await client.POST('/api/sessions/{session_id}/git/rebase/abort', { params });
  if (error !== undefined || data === undefined) throwSessionGitError(response, error);
  return data;
}

/** Start a fetch, pull or push; the answer is the job's first snapshot, not its result. */
export async function startSessionGitJob(
  agentId: string,
  sessionId: string,
  request: SessionGitSyncRequest,
): Promise<SessionGitJob> {
  const client = createAgentClient(agentId);
  const params = { path: { session_id: sessionId } };
  const result =
    request.op === 'fetch'
      ? await client.POST('/api/sessions/{session_id}/git/fetch', { params })
      : request.op === 'pull'
        ? await client.POST('/api/sessions/{session_id}/git/pull', {
            params,
            body: { mode: request.mode },
          })
        : await client.POST('/api/sessions/{session_id}/git/push', {
            params,
            body: { force: request.force },
          });
  const { data, error, response } = result;
  if (error !== undefined || data === undefined) throwSessionGitError(response, error);
  return data;
}

export async function getSessionGitJob(
  agentId: string,
  sessionId: string,
  jobId: string,
  signal?: AbortSignal,
): Promise<SessionGitJob> {
  const { data, error, response } = await createAgentClient(agentId).GET(
    '/api/sessions/{session_id}/git/jobs/{job_id}',
    { params: { path: { session_id: sessionId, job_id: jobId } }, signal },
  );
  if (error !== undefined || data === undefined) throwSessionGitError(response, error);
  return data;
}

export async function cancelSessionGitJob(
  agentId: string,
  sessionId: string,
  jobId: string,
): Promise<SessionGitJob> {
  const { data, error, response } = await createAgentClient(agentId).POST(
    '/api/sessions/{session_id}/git/jobs/{job_id}/cancel',
    { params: { path: { session_id: sessionId, job_id: jobId } } },
  );
  if (error !== undefined || data === undefined) throwSessionGitError(response, error);
  return data;
}

export async function getSessionGitConflict(
  agentId: string,
  sessionId: string,
  path: string,
  signal?: AbortSignal,
): Promise<SessionGitConflict> {
  const { data, error, response } = await createAgentClient(agentId).GET(
    '/api/sessions/{session_id}/git/conflict',
    { params: { path: { session_id: sessionId }, query: { path } }, signal },
  );
  if (error !== undefined || data === undefined) throwSessionGitError(response, error);
  return data;
}

export interface SessionGitResolveRequest {
  path: string;
  /** `GitConflictResponse.token` of the text the edit started from. */
  token: string;
  content: string;
  /** `false` saves a draft: the file is written, markers and all, and stays unmerged. */
  markResolved: boolean;
}

export async function resolveSessionGitConflict(
  agentId: string,
  sessionId: string,
  request: SessionGitResolveRequest,
): Promise<void> {
  const { error, response } = await createAgentClient(agentId).POST(
    '/api/sessions/{session_id}/git/conflict/resolve',
    {
      params: { path: { session_id: sessionId } },
      body: {
        path: request.path,
        token: request.token,
        content: request.content,
        mark_resolved: request.markResolved,
      },
    },
  );
  if (error !== undefined) throwSessionGitError(response, error);
}

export async function takeSessionGitConflict(
  agentId: string,
  sessionId: string,
  path: string,
  side: SessionGitConflictSide,
): Promise<void> {
  const { error, response } = await createAgentClient(agentId).POST(
    '/api/sessions/{session_id}/git/conflict/take',
    { params: { path: { session_id: sessionId } }, body: { path, side } },
  );
  if (error !== undefined) throwSessionGitError(response, error);
}

export interface SessionGitRestoreRequest {
  path: string;
  style: SessionGitConflictStyle;
  /** Set when the rebuild discards a file the user has open, so a concurrent edit refuses it. */
  token?: string;
}

export async function restoreSessionGitConflict(
  agentId: string,
  sessionId: string,
  request: SessionGitRestoreRequest,
): Promise<void> {
  const { error, response } = await createAgentClient(agentId).POST(
    '/api/sessions/{session_id}/git/conflict/restore',
    {
      params: { path: { session_id: sessionId } },
      body: { path: request.path, style: request.style, token: request.token ?? null },
    },
  );
  if (error !== undefined) throwSessionGitError(response, error);
}

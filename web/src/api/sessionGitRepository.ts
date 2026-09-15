/**
 * The session-scoped git Changes routes — the agent's working-tree status and
 * per-file diff for the directory a session was spawned in, and the
 * stage / unstage / discard / commit mutations of its staged set.
 *
 * REST-only shapes, so the types come straight from the generated schema like
 * `gitRepository`'s (the `OWNER.generated` policy in
 * `protocol/__fixtures__/coverage.ts`). The one hand-parsed body is the error
 * `detail`, which no schema documents: the two 404s share a status code and
 * differ only in FastAPI's string — a session lookup miss answers
 * `Session '<id>' not found`, while an agent that predates the routes answers
 * the relay's `Unknown proxy route` or a plain `Not Found` — and the git
 * failures carry `{"detail": {"code": …}}`. Android `SessionGitErrors.kt` and
 * iOS `SessionGitErrors.swift` draw the same lines.
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
  if (error.status === 404) {
    const detail = typeof error.detail === 'string' ? error.detail : null;
    return new SessionGitError(
      detail !== null && SESSION_NOT_FOUND.test(detail) ? 'sessionGone' : 'agentOutdated',
      error,
    );
  }
  if (typeof error.detail === 'object' && error.detail !== null) {
    const code = (error.detail as { code?: unknown }).code;
    if (typeof code === 'string') return new SessionGitError('git', error, code);
  }
  return error;
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

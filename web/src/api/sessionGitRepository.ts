/**
 * The session-scoped read-only git Changes routes — the agent's working-tree
 * status and per-file diff for the directory a session was spawned in.
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

export const GIT_CODE_UNAVAILABLE = 'git_unavailable';
export const GIT_CODE_TIMEOUT = 'git_timeout';
export const GIT_CODE_ERROR = 'git_error';

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

/**
 * Normalise the relay's error bodies into one shape.
 *
 * FastAPI hands back two different `detail` payloads depending on the
 * endpoint: a plain string (`{"detail": "Invalid email or password"}`) or a
 * structured object (`{"detail": {"error": "reset_code_expired"}}`). Callers
 * should never have to know which; they match on `code` and fall back to
 * `message`. This mirrors Android's `AuthRepository.mapResetError`.
 */
export class ApiError extends Error {
  readonly status: number;
  /** The `detail.error` discriminator, when the endpoint sends one. */
  readonly code: string | null;
  /** Seconds to wait, from `Retry-After` or a cooldown field in the body. */
  readonly retryAfterSeconds: number | null;
  readonly detail: unknown;

  constructor(init: {
    status: number;
    message: string;
    code: string | null;
    retryAfterSeconds: number | null;
    detail: unknown;
  }) {
    super(init.message);
    this.name = 'ApiError';
    this.status = init.status;
    this.code = init.code;
    this.retryAfterSeconds = init.retryAfterSeconds;
    this.detail = init.detail;
  }
}

/**
 * Raised when an admin account authenticates. Mirrors Android's
 * `AdminLoginNotSupportedException`: the check happens before any session is
 * stored, so an admin never reaches the agent screens that would 403 anyway
 * (`get_non_admin_api_user` guards every one of them).
 */
export class AdminLoginNotSupportedError extends Error {
  constructor() {
    super(ADMIN_LOGIN_MESSAGE);
    this.name = 'AdminLoginNotSupportedError';
  }
}

/** Verbatim from Android `AuthErrors.kt` / iOS `AuthErrors.swift`. */
export const ADMIN_LOGIN_MESSAGE =
  'This app is for agent owners. Admins should use the web dashboard.';

function readNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const parsed = Number.parseInt(value, 10);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

/**
 * Build an `ApiError` from an openapi-fetch failure.
 *
 * `body` is the parsed error payload; `response` supplies the status and the
 * `Retry-After` header the rate limiters set alongside their JSON cooldown.
 */
export function toApiError(response: Response, body: unknown): ApiError {
  const detail =
    typeof body === 'object' && body !== null && 'detail' in body
      ? body.detail
      : body;

  let code: string | null = null;
  let message: string | null = null;
  let retryAfterSeconds = readNumber(response.headers.get('Retry-After'));

  if (typeof detail === 'string') {
    message = detail;
  } else if (typeof detail === 'object' && detail !== null) {
    const fields = detail as Record<string, unknown>;
    if (typeof fields.error === 'string') code = fields.error;
    if (typeof fields.message === 'string') message = fields.message;
    retryAfterSeconds =
      retryAfterSeconds ??
      readNumber(fields.cooldown_seconds_remaining) ??
      readNumber(fields.retry_after_seconds);
  }

  return new ApiError({
    status: response.status,
    message: message ?? `Request failed (${String(response.status)})`,
    code,
    retryAfterSeconds,
    detail,
  });
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

/**
 * Whether an error means "this credential is no longer good".
 *
 * Used to keep TanStack Query from retrying — the default `retry` would turn
 * one dead token into a burst of identical 401s.
 */
export function isAuthError(error: unknown): boolean {
  return isApiError(error) && (error.status === 401 || error.status === 403);
}

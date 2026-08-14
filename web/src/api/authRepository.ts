/**
 * Auth endpoint wrappers.
 *
 * Every call that mints a session sends `refreshable: true` — the browser is
 * the client the server's opt-in refreshable session exists for. Mobile omits
 * the flag and keeps its long-lived token.
 *
 * It also sends `session_transport: 'cookie'`, which routes the refresh token
 * into an HttpOnly cookie instead of the response body. That is what lets a
 * session survive closing the last tab, and it means nothing here ever handles
 * the long-lived credential.
 */
import type { UserSession } from '../auth/sessionTransport';
import { relayClient } from './clients';
import { AdminLoginNotSupportedError, ApiError, toApiError } from './errors';

type AuthResponseBody = {
  access_token?: string | null;
  user_id: string;
  email: string;
  role: string;
  status: string;
  scheduled_deletion_at?: string | null;
};

/** Returned when the account exists but must verify its email before signing in. */
export class VerificationRequiredError extends Error {
  readonly email: string;
  constructor(email: string) {
    super('Verification required');
    this.name = 'VerificationRequiredError';
    this.email = email;
  }
}

export type AuthOutcome =
  | { kind: 'session'; session: UserSession }
  | { kind: 'verificationRequired'; email: string };

/**
 * Convert an auth response into a session, rejecting admins first.
 *
 * The role check happens before anything is stored, mirroring Android's
 * `finaliseOrFlagVerification`: every agent-facing route is guarded by
 * `get_non_admin_api_user`, so an admin who got a session here would land on
 * screens that 403 on every request.
 */
function toOutcome(body: AuthResponseBody): AuthOutcome {
  if (body.role === 'admin') {
    throw new AdminLoginNotSupportedError();
  }
  if (body.status === 'verification_required' || !body.access_token) {
    return { kind: 'verificationRequired', email: body.email };
  }
  return {
    kind: 'session',
    session: {
      accessToken: body.access_token,
      userId: body.user_id,
      email: body.email,
      role: body.role,
      scheduledDeletionAt: body.scheduled_deletion_at ?? null,
    },
  };
}

function fail(response: Response, error: unknown): never {
  throw toApiError(response, error);
}

export async function register(
  email: string,
  password: string,
): Promise<AuthOutcome> {
  const { data, error, response } = await relayClient.POST('/api/auth/register', {
    body: { email, password, refreshable: true, session_transport: 'cookie' },
  });
  if (error !== undefined || data === undefined) fail(response, error);
  return toOutcome(data);
}

export async function login(
  email: string,
  password: string,
): Promise<AuthOutcome> {
  const { data, error, response } = await relayClient.POST('/api/auth/login', {
    body: { email, password, refreshable: true, session_transport: 'cookie' },
  });
  if (error !== undefined || data === undefined) {
    const apiError = toApiError(response, error);
    // 403 carries `{"error": "verification_required", "email": …}` — a
    // recoverable state, not a failure: route to the code screen.
    if (apiError.status === 403 && apiError.code === 'verification_required') {
      const detail = apiError.detail as { email?: unknown } | null;
      const target = typeof detail?.email === 'string' ? detail.email : email;
      throw new VerificationRequiredError(target);
    }
    throw apiError;
  }
  return toOutcome(data);
}

export async function loginWithGoogle(idToken: string): Promise<AuthOutcome> {
  const { data, error, response } = await relayClient.POST('/api/auth/google', {
    body: { id_token: idToken, refreshable: true, session_transport: 'cookie' },
  });
  if (error !== undefined || data === undefined) fail(response, error);
  return toOutcome(data);
}

/**
 * `nonce` is required here where the Google twin sends none: the server
 * verifies it against the token's own claim on every Apple sign-in, not just
 * on re-auth.
 */
export async function loginWithApple(
  idToken: string,
  nonce: string,
): Promise<AuthOutcome> {
  const { data, error, response } = await relayClient.POST('/api/auth/apple', {
    body: { id_token: idToken, nonce, refreshable: true, session_transport: 'cookie' },
  });
  if (error !== undefined || data === undefined) fail(response, error);
  return toOutcome(data);
}

export async function verifyEmail(
  email: string,
  code: string,
): Promise<AuthOutcome> {
  const { data, error, response } = await relayClient.POST(
    '/api/auth/verify-email',
    { body: { email, code, refreshable: true, session_transport: 'cookie' } },
  );
  if (error !== undefined || data === undefined) fail(response, error);
  return toOutcome(data);
}

export interface ResendOutcome {
  /** Seconds the caller must wait before offering the button again. */
  cooldownSeconds: number;
  rateLimited: boolean;
}

/** Default cooldown, matching the server's per-email window. */
const RESEND_COOLDOWN_SECONDS = 60;

export async function resendVerification(email: string): Promise<ResendOutcome> {
  const { data, error, response } = await relayClient.POST(
    '/api/auth/resend-verification',
    { body: { email } },
  );
  if (error !== undefined || data === undefined) {
    const apiError = toApiError(response, error);
    if (apiError.status === 429) {
      return {
        cooldownSeconds: apiError.retryAfterSeconds ?? RESEND_COOLDOWN_SECONDS,
        rateLimited: true,
      };
    }
    throw apiError;
  }
  return { cooldownSeconds: RESEND_COOLDOWN_SECONDS, rateLimited: false };
}

export async function forgotPassword(email: string): Promise<void> {
  const { error, response } = await relayClient.POST(
    '/api/auth/forgot-password',
    { body: { email } },
  );
  if (error !== undefined) fail(response, error);
}

export async function resetPassword(
  email: string,
  code: string,
  newPassword: string,
): Promise<void> {
  const { error, response } = await relayClient.POST('/api/auth/reset-password', {
    body: { email, code, new_password: newPassword },
  });
  if (error !== undefined) fail(response, error);
}

export type DeletionMethod = 'password' | 'google' | 'apple';
export type DeletionMode = 'scheduled' | 'immediate';

export interface DeleteAccountInput {
  method: DeletionMethod;
  mode: DeletionMode;
  password?: string;
  idToken?: string;
  nonce?: string;
}

export async function deleteAccount(input: DeleteAccountInput): Promise<void> {
  const { error, response } = await relayClient.POST('/api/auth/me/delete', {
    body: {
      method: input.method,
      mode: input.mode,
      password: input.password ?? null,
      id_token: input.idToken ?? null,
      nonce: input.nonce ?? null,
    },
  });
  if (error !== undefined) fail(response, error);
}

/**
 * Revoke every browser session for this account, including this one.
 *
 * Through `relayClient`, unlike the per-browser `postLogout`: this one is
 * authenticated by the user token, so a 401 genuinely means the session is
 * already gone and the usual teardown should run.
 */
export async function signOutAllBrowsers(): Promise<void> {
  const { error, response } = await relayClient.POST('/api/auth/logout/all', {});
  if (error !== undefined) fail(response, error);
}

export async function cancelAccountDeletion(): Promise<void> {
  const { error, response } = await relayClient.POST(
    '/api/auth/me/delete/cancel',
    {},
  );
  if (error !== undefined) fail(response, error);
}

/**
 * User-facing copy for a delete failure.
 *
 * 400 is the seed-admin guard; 403 carries `{"error": "reauth_required",
 * "message": …}` where the server's message is specific enough to show
 * verbatim ("Incorrect password", "Google ID token is too old…").
 */
export function deleteAccountMessage(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return 'Could not delete your account. Check your connection and try again.';
  }
  if (error.status === 400) {
    return 'This account cannot be self-deleted (seed admin).';
  }
  if (error.status === 403) {
    return error.message;
  }
  return `Could not delete your account: ${error.message}`;
}

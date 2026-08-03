/**
 * Shared copy and error mapping for the auth screens.
 *
 * Strings are lifted verbatim from the Android composables (which hold them
 * inline — `strings.xml` has only `app_name`) and their iOS twins, so the
 * three clients read identically.
 */
import { ApiError, isApiError } from '../../api/errors';

/**
 * `alice@example.com` -> `a•••e@example.com`.
 *
 * Same rule as Android's `EmailFormatting.maskEmail` and its iOS twin: keep
 * the first character of the local part, keep the last one only when the local
 * part is long enough for the two not to be the same character, and never
 * touch the domain. The bullet count is `len - 2`, so "alice" yields three —
 * note the Kotlin KDoc's four-bullet example is wrong, as
 * `EmailFormattingTests.swift` records.
 */
export function maskEmail(email: string): string {
  const at = email.indexOf('@');
  if (at <= 0) return email;
  const local = email.slice(0, at);
  const domain = email.slice(at);
  if (local.length === 1) return `${local}•••${domain}`;
  if (local.length === 2) return `${local[0] ?? ''}•${domain}`;
  if (local.length === 3) return `${local[0] ?? ''}••${domain}`;
  const dots = '•'.repeat(Math.max(1, local.length - 2));
  return `${local[0] ?? ''}${dots}${local[local.length - 1] ?? ''}${domain}`;
}

/**
 * The IP-quota message shared by forgot-password, reset-password and the
 * reset resend. Lives here rather than inline at each call site so a wording
 * change cannot miss one of the three.
 */
export function networkRateLimitedMessage(seconds: number): string {
  return `Too many requests from this network. Try again in ${String(seconds)}s.`;
}

/** Verification-code failures. Mirrors Android's `friendlyApiMessage`. */
export function verificationMessage(error: unknown): string {
  if (!isApiError(error)) {
    return 'Verification failed. Check your connection and try again.';
  }
  switch (error.status) {
    case 400:
      return "That code isn't right. Try again.";
    case 410:
      return 'That code expired. Tap "Resend" to get a new one.';
    case 429:
      return 'Too many attempts. Request a new code.';
    default:
      return `Verification failed: ${error.message}`;
  }
}

/**
 * Password-reset failures. Mirrors Android's `AuthRepository.mapResetError`
 * table, including the two distinct 429s and the two distinct 400s.
 */
export function resetPasswordMessage(error: unknown): string {
  if (!isApiError(error)) {
    return "Couldn't reset password. Check your connection and try again.";
  }
  if (error.status === 410) {
    return 'That code expired. Tap "Resend" to get a new one.';
  }
  if (error.status === 429) {
    if (error.code === 'reset_ip_rate_limited') {
      return networkRateLimitedMessage(error.retryAfterSeconds ?? 60);
    }
    return 'Too many attempts. Request a new code.';
  }
  if (error.status === 400) {
    if (error.code === 'new_password_same_as_old') {
      return 'New password must differ from your current password.';
    }
    return "That code isn't right. Try again.";
  }
  return `Couldn't reset password: ${error.message}`;
}

/** True when the reset failure should also clear the code field. */
export function shouldClearCode(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.status === 400 &&
    error.code !== 'new_password_same_as_old'
  );
}

export function loginMessage(error: unknown): string {
  if (!isApiError(error)) {
    return 'Login failed. Check your connection and try again.';
  }
  if (error.status === 401) return 'Invalid email or password';
  return `Login failed: ${error.message}`;
}

export function registerMessage(error: unknown): string {
  if (!isApiError(error)) {
    return 'Registration failed. Check your connection and try again.';
  }
  if (error.status === 409) {
    return 'An account with this email already exists';
  }
  return `Registration failed: ${error.message}`;
}

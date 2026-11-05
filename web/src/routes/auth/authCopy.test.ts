import { describe, expect, it } from 'vitest';
import { ApiError } from '../../api/errors';
import {
  loginMessage,
  maskEmail,
  resetPasswordMessage,
  shouldClearCode,
  verificationMessage,
} from './authCopy';

function apiError(
  status: number,
  code: string | null = null,
  retryAfterSeconds: number | null = null,
): ApiError {
  return new ApiError({
    status,
    message: 'server said so',
    code,
    retryAfterSeconds,
    detail: null,
  });
}

describe('maskEmail', () => {
  it('keeps the first and last local characters for a long local part', () => {
    expect(maskEmail('alice@example.com')).toBe('a•••e@example.com');
  });

  it('pads a one-character local part instead of revealing its length', () => {
    expect(maskEmail('a@example.com')).toBe('a•••@example.com');
  });

  it('never reveals the last character of a two or three character local part', () => {
    // Parity with Android's `maskEmail`: below four characters the first and
    // last would give the whole local part away, so only the first survives.
    expect(maskEmail('ab@example.com')).toBe('a•@example.com');
    expect(maskEmail('abc@example.com')).toBe('a••@example.com');
  });

  it('switches to first-and-last at the four-character boundary', () => {
    expect(maskEmail('abcd@example.com')).toBe('a••d@example.com');
  });

  it('leaves a value that is not an email alone', () => {
    expect(maskEmail('no-at-sign')).toBe('no-at-sign');
    expect(maskEmail('@example.com')).toBe('@example.com');
  });
});

describe('resetPasswordMessage', () => {
  it('reports an expired code', () => {
    expect(resetPasswordMessage(apiError(410))).toBe(
      'That code expired. Tap "Resend" to get a new one.',
    );
  });

  it('reports the network rate limit with the server-supplied wait', () => {
    expect(resetPasswordMessage(apiError(429, 'reset_ip_rate_limited', 300))).toBe(
      'Too many requests from this network. Try again in 300s.',
    );
  });

  it('falls back to a 60s wait when the network rate limit sends none', () => {
    expect(resetPasswordMessage(apiError(429, 'reset_ip_rate_limited'))).toBe(
      'Too many requests from this network. Try again in 60s.',
    );
  });

  it('separates a per-code 429 from the per-network one', () => {
    expect(resetPasswordMessage(apiError(429))).toBe(
      'Too many attempts. Request a new code.',
    );
  });

  it('separates the two 400s', () => {
    expect(resetPasswordMessage(apiError(400, 'new_password_same_as_old'))).toBe(
      'New password must differ from your current password.',
    );
    expect(resetPasswordMessage(apiError(400))).toBe(
      "That code isn't right. Try again.",
    );
  });

  it('treats a transport failure as a connection problem', () => {
    expect(resetPasswordMessage(new TypeError('Failed to fetch'))).toBe(
      "Couldn't reset password. Check your connection and try again.",
    );
  });
});

describe('shouldClearCode', () => {
  it('clears the field only for a wrong code', () => {
    expect(shouldClearCode(apiError(400))).toBe(true);
  });

  it('keeps the code when only the new password was rejected', () => {
    // The code is still good — re-typing it would be busywork for the user.
    expect(shouldClearCode(apiError(400, 'new_password_same_as_old'))).toBe(false);
  });

  it('keeps the code for failures that are not a code rejection', () => {
    expect(shouldClearCode(apiError(410))).toBe(false);
    expect(shouldClearCode(apiError(429))).toBe(false);
    expect(shouldClearCode(new TypeError('Failed to fetch'))).toBe(false);
  });
});

describe('verificationMessage', () => {
  it('maps each verification failure status to its own copy', () => {
    expect(verificationMessage(apiError(400))).toBe(
      "That code isn't right. Try again.",
    );
    expect(verificationMessage(apiError(410))).toBe(
      'That code expired. Tap "Resend" to get a new one.',
    );
    expect(verificationMessage(apiError(429))).toBe(
      'Too many attempts. Request a new code.',
    );
  });

  it('surfaces the server message for an unmapped status', () => {
    expect(verificationMessage(apiError(500))).toBe(
      'Verification failed: server said so',
    );
  });
});

describe('loginMessage', () => {
  it('reports the sign-in lockout with the wait rounded up to minutes', () => {
    expect(loginMessage(apiError(429, 'login_rate_limited', 900))).toBe(
      'Too many sign-in attempts. Try again in 15 minutes, or reset your password.',
    );
    expect(loginMessage(apiError(429, 'login_rate_limited', 61))).toBe(
      'Too many sign-in attempts. Try again in 2 minutes, or reset your password.',
    );
  });

  it('says one minute, not one minutes, for a wait under a minute', () => {
    expect(loginMessage(apiError(429, 'login_rate_limited', 30))).toBe(
      'Too many sign-in attempts. Try again in 1 minute, or reset your password.',
    );
  });

  it("falls back to the relay's 15-minute window when the 429 sends no wait", () => {
    expect(loginMessage(apiError(429))).toBe(
      'Too many sign-in attempts. Try again in 15 minutes, or reset your password.',
    );
  });

  it('keeps the wrong-password copy for a 401', () => {
    expect(loginMessage(apiError(401))).toBe('Invalid email or password');
  });
});

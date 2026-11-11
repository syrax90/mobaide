import { describe, expect, it } from 'vitest';
import { isAuthError, toApiError } from './errors';

function errorResponse(
  status: number,
  headers: Record<string, string> = {},
): Response {
  return new Response(null, { status, headers });
}

describe('toApiError detail shapes', () => {
  it('uses a plain string detail as the message', () => {
    const error = toApiError(errorResponse(401), {
      detail: 'Invalid email or password',
    });

    expect(error.status).toBe(401);
    expect(error.message).toBe('Invalid email or password');
    expect(error.code).toBeNull();
  });

  it('splits a structured detail into code and message', () => {
    const error = toApiError(errorResponse(403), {
      detail: { error: 'reauth_required', message: 'Incorrect password' },
    });

    expect(error.code).toBe('reauth_required');
    expect(error.message).toBe('Incorrect password');
  });

  it('keeps the code when a structured detail carries no message', () => {
    const error = toApiError(errorResponse(400), {
      detail: { error: 'verification_code_invalid' },
    });

    expect(error.code).toBe('verification_code_invalid');
    expect(error.message).toBe('Request failed (400)');
  });

  it('falls back to the status when the body carries no detail at all', () => {
    const error = toApiError(errorResponse(502), undefined);

    expect(error.message).toBe('Request failed (502)');
    expect(error.code).toBeNull();
    expect(error.retryAfterSeconds).toBeNull();
  });
});

describe('toApiError retry-after resolution', () => {
  it('reads the Retry-After header', () => {
    const error = toApiError(errorResponse(429, { 'Retry-After': '45' }), {
      detail: { error: 'reset_ip_rate_limited' },
    });

    expect(error.retryAfterSeconds).toBe(45);
  });

  it('prefers the header over a cooldown field in the body', () => {
    const error = toApiError(errorResponse(429, { 'Retry-After': '45' }), {
      detail: { error: 'resend_rate_limited', cooldown_seconds_remaining: 12 },
    });

    expect(error.retryAfterSeconds).toBe(45);
  });

  it('falls back to cooldown_seconds_remaining when no header is sent', () => {
    const error = toApiError(errorResponse(429), {
      detail: { error: 'resend_rate_limited', cooldown_seconds_remaining: 12 },
    });

    expect(error.retryAfterSeconds).toBe(12);
  });

  it('falls back to retry_after_seconds when no header is sent', () => {
    const error = toApiError(errorResponse(429), {
      detail: { error: 'reset_ip_rate_limited', retry_after_seconds: 300 },
    });

    expect(error.retryAfterSeconds).toBe(300);
  });

  it('ignores an HTTP-date Retry-After rather than reporting NaN seconds', () => {
    const error = toApiError(
      errorResponse(429, { 'Retry-After': 'Wed, 21 Oct 2026 07:28:00 GMT' }),
      { detail: { error: 'reset_ip_rate_limited', retry_after_seconds: 30 } },
    );

    expect(error.retryAfterSeconds).toBe(30);
  });
});

describe('isAuthError', () => {
  it('is true only for the credential-is-dead statuses', () => {
    expect(isAuthError(toApiError(errorResponse(401), {}))).toBe(true);
    expect(isAuthError(toApiError(errorResponse(403), {}))).toBe(true);
    expect(isAuthError(toApiError(errorResponse(429), {}))).toBe(false);
    expect(isAuthError(toApiError(errorResponse(500), {}))).toBe(false);
  });
});

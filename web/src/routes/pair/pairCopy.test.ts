import { describe, expect, it } from 'vitest';
import { ApiError } from '../../api/errors';
import {
  TRUST_PIN_MISMATCH_CODE,
  WAIT_FOR_ONLINE_TIMEOUT_SECONDS,
  claimErrorMessage,
  deviceCodeErrorMessage,
  expiredMessage,
  expiresInMessage,
  trustPinMismatchMessage,
  waitTimeoutMessage,
} from './pairCopy';

const NOW = 1_700_000_000;

/** Same shape `toApiError` builds, minus the fields this copy never reads. */
function apiError(status: number, message: string, code: string | null = null): ApiError {
  return new ApiError({
    status,
    message,
    code,
    retryAfterSeconds: null,
    detail: message,
  });
}

describe('expiredMessage', () => {
  it.each([
    [NOW - 59, 'less than a minute ago'],
    [NOW - 60, '1 minute ago'],
    [NOW - 60 * 120, '2 hours ago'],
  ])('describes an exp of now-%i as %s', (expUnix, expected) => {
    expect(expiredMessage(expUnix, NOW)).toContain(expected);
  });

  it('never reports a negative age for a clock that ran backwards', () => {
    // A browser whose clock is behind the agent's can see `now < exp` on a
    // code the state machine already called stale. "-3 minutes ago" would
    // read as a bug in the product rather than a bug in the clock.
    expect(expiredMessage(NOW + 300, NOW)).toContain('less than a minute ago');
  });

});

describe('expiresInMessage', () => {
  it.each([
    [NOW + 59, 'in under a minute'],
    [NOW + 60, 'in 1 minute'],
    [NOW + 120, 'in 2 minutes'],
  ])('describes an exp of now+%i as %s', (expUnix, expected) => {
    expect(expiresInMessage(expUnix, NOW)).toBe(expected);
  });

  it('clamps an already-passed expiry instead of counting down past zero', () => {
    expect(expiresInMessage(NOW - 600, NOW)).toBe('in under a minute');
  });
});

describe('claimErrorMessage', () => {
  it.each([
    [400, 'older relay trust key'],
    [410, 'consumed or expired'],
    [503, 'not configured on this relay'],
  ])('names a remedy for %i', (status, fragment) => {
    // Both mobile clients pass the server `detail` through for everything but
    // 410. A desktop user is often not sitting at the agent host, and none of
    // those details name a next step, so each status gets copy that does.
    expect(claimErrorMessage(apiError(status, 'raw server detail'))).toContain(fragment);
  });

  it('passes an unmapped status detail through', () => {
    expect(claimErrorMessage(apiError(418, 'teapot'))).toBe('teapot');
  });

  it('falls back to a generic line when an unmapped status carries no detail', () => {
    expect(claimErrorMessage(apiError(500, ''))).toBe('Pairing failed.');
  });

  it('handles a thrown non-Error', () => {
    expect(claimErrorMessage('nope')).toBe('Pairing failed.');
  });
});

describe('deviceCodeErrorMessage', () => {
  it.each([
    [410, 'expired or was already used'],
    [429, 'Too many pairing attempts'],
  ])('names a remedy for %i', (status, fragment) => {
    expect(deviceCodeErrorMessage(apiError(status, 'raw server detail'))).toContain(
      fragment,
    );
  });

  it('reads the account-pin refusal off the code, not the status', () => {
    // 409 already means "that machine belongs to another account" here, so
    // only the discriminator separates the two.
    const pinned = apiError(409, 'raw server detail', TRUST_PIN_MISMATCH_CODE);

    expect(deviceCodeErrorMessage(pinned)).toBe(trustPinMismatchMessage());
    expect(deviceCodeErrorMessage(pinned)).not.toContain('different account');
  });

  it('passes an unmapped status detail through', () => {
    expect(deviceCodeErrorMessage(apiError(418, 'teapot'))).toBe('teapot');
  });

  it('falls back to a generic line when an unmapped status carries no detail', () => {
    expect(deviceCodeErrorMessage(apiError(500, ''))).toBe('Pairing failed.');
  });

  it('handles a thrown non-Error', () => {
    expect(deviceCodeErrorMessage('nope')).toBe('Pairing failed.');
  });
});

describe('trustPinMismatchMessage', () => {
  it('is reachable from the QR claim path too', () => {
    // The account pin is enforced in the shared completion path, so a QR
    // claim can hit it as readily as a device-code confirm.
    expect(
      claimErrorMessage(apiError(409, 'raw detail', TRUST_PIN_MISMATCH_CODE)),
    ).toBe(trustPinMismatchMessage());
  });
});

describe('waitTimeoutMessage', () => {
  it('appends nothing at all when ws_delivered is absent', () => {
    // Tri-state, and `null` must never collapse to `false`: an older relay
    // omits the field, and claiming credentials "were staged" on that
    // evidence would send the user chasing a reconnect that never happened.
    const message = waitTimeoutMessage('Workstation', null);

    expect(message).not.toContain('staged');
    expect(message).not.toContain('tunnel');
    expect(message).toContain('Workstation');
    expect(message).toContain(String(WAIT_FOR_ONLINE_TIMEOUT_SECONDS));
    expect(message).toContain('mobaide-agent show-qr');
  });

  it('says the push landed when ws_delivered is true', () => {
    const message = waitTimeoutMessage('Workstation', true);

    expect(message).toContain('tunnel');
    expect(message).not.toContain('staged');
  });

  it('says the push was staged when ws_delivered is false', () => {
    const message = waitTimeoutMessage('Workstation', false);

    expect(message).toContain('staged');
    expect(message).not.toContain('tunnel');
  });

});

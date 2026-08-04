import { describe, expect, it } from 'vitest';
import { ApiError } from '../../api/errors';
import {
  WAIT_FOR_ONLINE_TIMEOUT_SECONDS,
  claimErrorMessage,
  expiredMessage,
  expiresInMessage,
  waitTimeoutMessage,
} from './pairCopy';

const NOW = 1_700_000_000;

/** Same shape `toApiError` builds, minus the fields this copy never reads. */
function apiError(status: number, message: string): ApiError {
  return new ApiError({
    status,
    message,
    code: null,
    retryAfterSeconds: null,
    detail: message,
  });
}

describe('expiredMessage', () => {
  it.each([
    [NOW - 5, 'less than a minute ago'],
    [NOW - 59, 'less than a minute ago'],
    [NOW - 60, '1 minute ago'],
    [NOW - 119, '1 minute ago'],
    [NOW - 120, '2 minutes ago'],
    [NOW - 60 * 119, '119 minutes ago'],
    [NOW - 60 * 120, '2 hours ago'],
    [NOW - 60 * 60 * 25, '25 hours ago'],
  ])('describes an exp of now-%i as %s', (expUnix, expected) => {
    expect(expiredMessage(expUnix, NOW)).toContain(expected);
  });

  it('never reports a negative age for a clock that ran backwards', () => {
    // A browser whose clock is behind the agent's can see `now < exp` on a
    // code the state machine already called stale. "-3 minutes ago" would
    // read as a bug in the product rather than a bug in the clock.
    expect(expiredMessage(NOW + 300, NOW)).toContain('less than a minute ago');
  });

  it('tells the user how to get a fresh code', () => {
    expect(expiredMessage(NOW - 60, NOW)).toContain('mobaide-agent show-qr');
  });
});

describe('expiresInMessage', () => {
  it.each([
    [NOW + 1, 'in under a minute'],
    [NOW + 59, 'in under a minute'],
    [NOW + 60, 'in 1 minute'],
    [NOW + 119, 'in 1 minute'],
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
    [404, 'no longer exists'],
    [409, 'different account'],
    [410, 'consumed or expired'],
    [503, 'not configured on this relay'],
  ])('names a remedy for %i', (status, fragment) => {
    // Both mobile clients pass the server `detail` through for everything but
    // 410. A desktop user is often not sitting at the agent host, and none of
    // those details name a next step, so each status gets copy that does.
    expect(claimErrorMessage(apiError(status, 'raw server detail'))).toContain(fragment);
  });

  it('uses the mobile wording verbatim for a consumed nonce', () => {
    expect(claimErrorMessage(apiError(410, 'gone'))).toBe(
      'Pairing code consumed or expired. Render a fresh QR on the agent.',
    );
  });

  it('passes an unmapped status detail through', () => {
    expect(claimErrorMessage(apiError(418, 'teapot'))).toBe('teapot');
  });

  it('falls back to a generic line when an unmapped status carries no detail', () => {
    expect(claimErrorMessage(apiError(500, ''))).toBe('Pairing failed.');
  });

  it('handles a transport failure that never reached the relay', () => {
    expect(claimErrorMessage(new TypeError('Failed to fetch'))).toBe('Failed to fetch');
  });

  it('handles a thrown non-Error', () => {
    expect(claimErrorMessage('nope')).toBe('Pairing failed.');
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

  it('keeps the browser-neutral short form, not Android Windows-only steps', () => {
    // Android's version enumerates `Get-ScheduledTask MobaideAgent` and
    // `agent.err.log` under `$env:ProgramFiles`, which is right for a phone
    // held next to a desktop and wrong for a browser whose host is as likely
    // to be Linux.
    const message = waitTimeoutMessage('Workstation', true);

    expect(message).not.toContain('Get-ScheduledTask');
    expect(message).not.toContain('ProgramFiles');
  });
});

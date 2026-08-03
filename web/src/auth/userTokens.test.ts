import { afterEach, describe, expect, it, vi } from 'vitest';
import { expiresInSeconds, isAccessTokenExpiringSoon } from './userTokens';

const NOW_SECONDS = 1_800_000_000;

function base64Url(value: string): string {
  return btoa(value).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/** A JWT with only the parts the client parses — the signature is never read. */
function jwt(claims: Record<string, unknown>): string {
  return `${base64Url('{"alg":"HS256"}')}.${base64Url(JSON.stringify(claims))}.sig`;
}

function freezeClock() {
  vi.useFakeTimers();
  vi.setSystemTime(NOW_SECONDS * 1000);
}

afterEach(() => {
  vi.useRealTimers();
});

describe('expiresInSeconds', () => {
  it('reports the seconds left on a token that expires', () => {
    freezeClock();

    expect(expiresInSeconds(jwt({ exp: NOW_SECONDS + 900 }))).toBe(900);
  });

  it('reports a negative remainder for an already-expired token', () => {
    freezeClock();

    expect(expiresInSeconds(jwt({ exp: NOW_SECONDS - 5 }))).toBe(-5);
  });

  it('reports null when the token carries no exp claim', () => {
    // The server omits `exp` entirely when the TTL is 0, which is the default
    // for a non-refreshable session — that token never expires.
    expect(expiresInSeconds(jwt({ sub: 'user-1', role: 'viewer' }))).toBeNull();
  });

  it('reports null when exp is not a number', () => {
    expect(expiresInSeconds(jwt({ exp: '1800000900' }))).toBeNull();
  });

  it('reports expired for a token this client cannot parse', () => {
    expect(expiresInSeconds('not-a-jwt')).toBe(0);
    expect(expiresInSeconds(`${base64Url('{"alg":"HS256"}')}.@@@.sig`)).toBe(0);
    expect(expiresInSeconds(`${base64Url('{"alg":"HS256"}')}.${base64Url('7')}.sig`)).toBe(0);
  });
});

describe('isAccessTokenExpiringSoon', () => {
  it('is true inside the 60s refresh leeway', () => {
    freezeClock();

    expect(isAccessTokenExpiringSoon(jwt({ exp: NOW_SECONDS + 59 }))).toBe(true);
  });

  it('is false outside the leeway', () => {
    freezeClock();

    expect(isAccessTokenExpiringSoon(jwt({ exp: NOW_SECONDS + 61 }))).toBe(false);
  });

  it('is false for a token with no expiry', () => {
    expect(isAccessTokenExpiringSoon(jwt({ sub: 'user-1' }))).toBe(false);
  });

  it('is true for an unparseable token so the caller refreshes it', () => {
    expect(isAccessTokenExpiringSoon('not-a-jwt')).toBe(true);
  });
});

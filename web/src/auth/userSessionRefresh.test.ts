import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubWebStorage } from '../test/localStorageStub';
import type { UserSession } from './sessionTransport';

// Before importing anything that captures either global.
stubWebStorage('sessionStorage');
stubWebStorage('localStorage');

const { refreshUserSession } = await import('./userTokens');
const { useSessionStore } = await import('./sessionStore');
const { resetSessionTransport } = await import('./sessionTransport');
const { resetSingleFlight } = await import('./singleFlight');

const NOW_SECONDS = 1_800_000_000;

function base64Url(value: string): string {
  return btoa(value).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/** A JWT with only the parts the client parses — the signature is never read. */
function jwt(expiresInSeconds: number): string {
  return `${base64Url('{"alg":"HS256"}')}.${base64Url(
    JSON.stringify({ exp: NOW_SECONDS + expiresInSeconds }),
  )}.sig`;
}

function session(accessToken: string): UserSession {
  return {
    accessToken,
    userId: 'user-1',
    email: 'user@example.com',
    role: 'viewer',
    scheduledDeletionAt: null,
  };
}

/** Only the members the refresh path reads. */
function httpResponse(status: number, body?: unknown): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

/**
 * A 401 that lands *after* a peer tab's rotation broadcast has been applied —
 * the race the branch under test exists for.
 */
function stubLostRace(peer: UserSession | null) {
  const fetchMock = vi.fn(() => {
    if (peer !== null) useSessionStore.getState().applyRotatedSession(peer);
    return Promise.resolve(httpResponse(401));
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW_SECONDS * 1000);
  resetSessionTransport();
  resetSingleFlight();
  sessionStorage.clear();
  localStorage.clear();
  useSessionStore.setState({
    authState: { kind: 'unauthenticated' },
    session: null,
    hydrated: true,
    lastInvalidation: null,
  });
});

afterEach(() => {
  vi.useRealTimers();
});

describe('refreshUserSession', () => {
  it('is terminal without a request when the tab holds no session', async () => {
    // A rotation queued behind the cross-tab lock through a sign-out would
    // otherwise spend the cookie and land a fresh 30-day credential in the jar
    // — which the next boot would silently restore.
    const fetchMock = stubLostRace(null);

    await expect(refreshUserSession()).resolves.toEqual({ kind: 'terminal' });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('adopts the winner of a lost race rather than signing every tab out', async () => {
    const peer = session(jwt(900));
    useSessionStore.getState().markUserLoggedIn(session(jwt(10)));
    stubLostRace(peer);

    const outcome = await refreshUserSession();

    expect(outcome).toEqual({ kind: 'success', session: peer });
    expect(useSessionStore.getState().session).toEqual(peer);
  });

  it('is terminal when the token that came back is itself near expiry', async () => {
    // "Different" would otherwise just mean "also stale", and the tab would
    // keep a session it has no way to renew.
    useSessionStore.getState().markUserLoggedIn(session(jwt(10)));
    stubLostRace(session(jwt(30)));

    const outcome = await refreshUserSession();

    expect(outcome).toEqual({ kind: 'terminal' });
  });

  it('is terminal when no peer rotated anything under it', async () => {
    useSessionStore.getState().markUserLoggedIn(session(jwt(10)));
    stubLostRace(null);

    const outcome = await refreshUserSession();

    expect(outcome).toEqual({ kind: 'terminal' });
  });

  it('sends no refresh token of its own — the cookie is the credential', async () => {
    useSessionStore.getState().markUserLoggedIn(session(jwt(10)));
    const fetchMock = stubLostRace(null);

    await refreshUserSession();

    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.body).toBeUndefined();
    expect(init.credentials).toBe('same-origin');
    expect(init.headers).toEqual({ 'X-Mobaide-Session': '1' });
  });
});

describe('sign-out revocation', () => {
  it('revokes the cookie on a deliberate sign-out and not on a rejected token', async () => {
    // `manual` is the user ending the session. `tokenRejected` may be this tab
    // reacting to a token that is already dead, and revoking on it would tear
    // down a cookie a different account's fresh tab has since installed.
    const fetchMock = vi.fn(
      (url: string): Promise<Response> => {
        void url;
        return Promise.resolve({ status: 204, ok: true } as Response);
      },
    );
    vi.stubGlobal('fetch', fetchMock);

    useSessionStore.setState({
      authState: { kind: 'userOnly' },
      session: {
        accessToken: 'a',
        userId: 'user-1',
        email: 'u@e.co',
        role: 'viewer',
        scheduledDeletionAt: null,
      },
    });
    useSessionStore.getState().invalidateUserSession('tokenRejected');
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).not.toHaveBeenCalled();

    useSessionStore.setState({
      authState: { kind: 'userOnly' },
      session: {
        accessToken: 'a',
        userId: 'user-1',
        email: 'u@e.co',
        role: 'viewer',
        scheduledDeletionAt: null,
      },
    });
    useSessionStore.getState().invalidateUserSession('manual');
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/auth/logout');
  });
});

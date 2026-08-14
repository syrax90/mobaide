import { beforeEach, describe, expect, it, vi } from 'vitest';
import { stubWebStorage } from '../test/localStorageStub';

// Before importing anything that captures either global.
stubWebStorage('sessionStorage');
stubWebStorage('localStorage');

const { bootstrapSession, resetSessionBootstrap, useSessionStore } =
  await import('./sessionStore');
const { resetSessionTransport } = await import('./sessionTransport');
const { resetSingleFlight } = await import('./singleFlight');

const STORAGE_KEY = 'mobaide-session';
const AGENT_TOKENS = 'mobaide-agent-tokens';
const RECENTS = 'recent_custom_paths::agent-1';

// The prefixed name is the one only this origin can have written, so it is
// the one that licenses a destructive purge. jsdom does not implement cookie
// prefixes, so it round-trips here even over http.
const MARKER = '__Host-mobaide_signed_in';
const PLANTABLE_MARKER = 'mobaide_signed_in';

/** Only the four members `readRefreshOutcome` reads. */
function httpResponse(status: number, body?: unknown): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

function restoredBody() {
  return {
    access_token: 'access-restored',
    user_id: 'user-1',
    email: 'user@example.com',
    role: 'viewer',
    scheduled_deletion_at: null,
  };
}

function stubFetch(response: Response) {
  const fetchMock = vi.fn(() => Promise.resolve(response));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function seedLocalState(): void {
  localStorage.setItem(AGENT_TOKENS, JSON.stringify({ 'agent-1': 'token' }));
  localStorage.setItem(RECENTS, JSON.stringify(['/srv/app']));
}

beforeEach(() => {
  resetSessionTransport();
  resetSessionBootstrap();
  resetSingleFlight();
  sessionStorage.clear();
  localStorage.clear();
  // A `__Host-` deletion has to repeat the attributes it was set under, or
  // jsdom rejects it as a prefix violation and the cookie survives the reset.
  document.cookie = `${MARKER}=; Max-Age=0; Path=/; Secure`;
  document.cookie = `${PLANTABLE_MARKER}=; Max-Age=0; Path=/`;
  useSessionStore.setState({
    authState: { kind: 'unauthenticated' },
    session: null,
    hydrated: false,
    lastInvalidation: null,
  });
});

describe('bootstrapSession cookie restore', () => {
  it('signs the tab in from the refresh cookie when it holds no session', async () => {
    // The whole point of the change: sessionStorage is empty because every tab
    // was closed, and the session comes back anyway.
    document.cookie = `${MARKER}=1; Path=/; Secure`;
    const fetchMock = stubFetch(httpResponse(200, restoredBody()));

    await bootstrapSession();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const state = useSessionStore.getState();
    expect(state.hydrated).toBe(true);
    expect(state.authState).toEqual({ kind: 'userOnly' });
    expect(state.session).toEqual({
      accessToken: 'access-restored',
      userId: 'user-1',
      email: 'user@example.com',
      role: 'viewer',
      scheduledDeletionAt: null,
    });
    expect(sessionStorage.getItem(STORAGE_KEY)).toBe(
      JSON.stringify(state.session),
    );
  });

  it('sends the cookie and the origin proof, and no body', async () => {
    // The server's CSRF gate 403s a cookie-path refresh without the header,
    // and reads the credential off the jar rather than the request.
    document.cookie = `${MARKER}=1; Path=/; Secure`;
    const fetchMock = stubFetch(httpResponse(200, restoredBody()));

    await bootstrapSession();

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/auth/refresh');
    expect(init.method).toBe('POST');
    expect(init.headers).toEqual({ 'X-Mobaide-Session': '1' });
    expect(init.credentials).toBe('same-origin');
    expect(init.body).toBeUndefined();
  });

  it('settles signed out without a single request when no marker exists', async () => {
    // A visitor who never signed in must not pay a round trip on every load.
    const fetchMock = stubFetch(httpResponse(200, restoredBody()));

    await bootstrapSession();

    expect(fetchMock).not.toHaveBeenCalled();
    const state = useSessionStore.getState();
    expect(state.hydrated).toBe(true);
    expect(state.authState).toEqual({ kind: 'unauthenticated' });
    expect(state.session).toBeNull();
  });

  it('keeps recents when no marker exists, dropping only the agent tokens', async () => {
    // No marker is not a server verdict — nothing was ever asked. It is also
    // what a cookie-blocked browser looks like on every single boot, so
    // purging recents here would quietly punish those users for a browser
    // setting. Agent tokens still go, exactly as they did before the cookie
    // existed: they are stale and re-mintable, a typed directory is neither.
    seedLocalState();
    stubFetch(httpResponse(200, restoredBody()));

    await bootstrapSession();

    expect(useSessionStore.getState().session).toBeNull();
    expect(localStorage.getItem(AGENT_TOKENS)).toBeNull();
    expect(localStorage.getItem(RECENTS)).toBe(JSON.stringify(['/srv/app']));
  });

  it('purges agent tokens and recents when the restore is terminal', async () => {
    seedLocalState();
    document.cookie = `${MARKER}=1; Path=/; Secure`;
    stubFetch(httpResponse(401));

    await bootstrapSession();

    expect(useSessionStore.getState().session).toBeNull();
    expect(localStorage.getItem(AGENT_TOKENS)).toBeNull();
    expect(localStorage.getItem(RECENTS)).toBeNull();
  });

  it('attempts a restore on a plantable marker but will not purge on it', async () => {
    // `mobaide_signed_in` (no `__Host-`) is what the server emits when
    // USER_SESSION_COOKIE_SECURE is false, so it must still drive a restore —
    // a build-time guess at the name would resurrect the original bug. But any
    // sibling host on the registrable domain can also plant it, and it must not
    // be able to destroy recents on every boot by doing so.
    seedLocalState();
    document.cookie = `${PLANTABLE_MARKER}=1; Path=/`;
    const fetchMock = stubFetch(httpResponse(401));

    await bootstrapSession();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem(AGENT_TOKENS)).toBeNull();
    expect(localStorage.getItem(RECENTS)).toBe(JSON.stringify(['/srv/app']));
  });

  it('will not read a unicode-padded name as the trusted marker', async () => {
    // A browser stores a Unicode-space-padded `__Host-mobaide_signed_in`
    // verbatim — the prefix rules never fire, because the name does not
    // literally begin with `__Host-` — so a sibling host can set it with a
    // `Domain` and have it reach this origin. `trim()` strips every Unicode
    // space and would normalize it straight back into the trusted name.
    //
    // The jar is stubbed rather than assigned: jsdom sanitizes the name on
    // write, so `document.cookie = ...` would never reproduce what a real
    // browser hands back, and the test would pass against the broken parse.
    seedLocalState();
    const spy = vi
      .spyOn(document, 'cookie', 'get')
      .mockReturnValue('\u2000' + MARKER + '=1');
    stubFetch(httpResponse(401));

    await bootstrapSession();

    expect(localStorage.getItem(RECENTS)).toBe(JSON.stringify(['/srv/app']));
    spy.mockRestore();
  });

  it('will not read a nameless cookie as the trusted marker', async () => {
    // `Set-Cookie: __Host-mobaide_signed_in` (no `=`) parses as an empty name
    // with that string as its *value*, and serializes back as a bare token.
    // Splitting on `=` and taking [0] would read the value as a name.
    seedLocalState();
    document.cookie = MARKER;
    stubFetch(httpResponse(401));

    await bootstrapSession();

    expect(localStorage.getItem(RECENTS)).toBe(JSON.stringify(['/srv/app']));
  });

  it('keeps both when the restore only fails transiently', async () => {
    // A 503 is a user on a subway, not an identity that ended. Agent tokens
    // can be re-minted; a typed directory cannot.
    seedLocalState();
    document.cookie = `${MARKER}=1; Path=/; Secure`;
    stubFetch(httpResponse(503));

    await bootstrapSession();

    const state = useSessionStore.getState();
    expect(state.hydrated).toBe(true);
    expect(state.authState).toEqual({ kind: 'unauthenticated' });
    expect(localStorage.getItem(AGENT_TOKENS)).toBe(
      JSON.stringify({ 'agent-1': 'token' }),
    );
    expect(localStorage.getItem(RECENTS)).toBe(JSON.stringify(['/srv/app']));
  });
});

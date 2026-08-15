import { beforeEach, describe, expect, it, vi } from 'vitest';
import { stubWebStorage } from '../test/localStorageStub';
import type { UserSession } from './sessionTransport';

// Before importing anything that captures either global.
stubWebStorage('sessionStorage');
stubWebStorage('localStorage');

const { useSessionStore, bootstrapSession, resetSessionBootstrap } =
  await import('./sessionStore');
const { resetSessionTransport } = await import('./sessionTransport');

const AGENT_TOKENS = 'mobaide-agent-tokens';
const RECENTS = 'recent_custom_paths::agent-1';
// jsdom does not implement cookie prefixes, so the `__Host-` name round-trips
// here over http — see `sessionCookieRestore.test.ts`.
const MARKER = '__Host-mobaide_signed_in';

const session: UserSession = {
  accessToken: 'access-1',
  userId: 'user-1',
  email: 'user@example.com',
  role: 'viewer',
  scheduledDeletionAt: null,
};

function stubFetch() {
  const fetchMock = vi.fn(
    (url: string): Promise<Response> =>
      Promise.resolve({ status: 204, ok: true, url } as unknown as Response),
  );
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function logoutCalls(fetchMock: ReturnType<typeof stubFetch>): string[] {
  return fetchMock.mock.calls
    .map(([url]) => url)
    .filter((url) => url === '/api/auth/logout');
}

function seedLocalState(): void {
  localStorage.setItem(AGENT_TOKENS, JSON.stringify({ 'agent-1': 'token' }));
  localStorage.setItem(RECENTS, JSON.stringify(['/srv/app']));
}

beforeEach(() => {
  resetSessionTransport();
  resetSessionBootstrap();
  sessionStorage.clear();
  localStorage.clear();
  document.cookie = `${MARKER}=; Max-Age=0; Path=/; Secure`;
  useSessionStore.setState({
    authState: { kind: 'unauthenticated' },
    session: null,
    hydrated: false,
    lastInvalidation: null,
  });
});

describe('a session torn down because deletion was scheduled', () => {
  it('purges the recents and agent tokens the identity owned', () => {
    // The reason was spelled `manual` until it grew its own login-screen
    // notice; splitting it off is only safe if it stayed on the identity-ending
    // side of the ledger. The paths name the user's own workstation and cannot
    // be re-minted, so an account on its way out must not leave them for the
    // next account in this browser.
    seedLocalState();
    stubFetch();
    useSessionStore.getState().markUserLoggedIn(session);

    useSessionStore.getState().invalidateUserSession('deletionScheduled');

    expect(localStorage.getItem(RECENTS)).toBeNull();
    expect(localStorage.getItem(AGENT_TOKENS)).toBeNull();
  });

  it('revokes the refresh cookie and drops the marker that would restore it', () => {
    // The cookie outlives the tab by design. Left alone it restores the session
    // of an account the user just asked us to delete, on the next load and for
    // the cookie's full lifetime.
    document.cookie = `${MARKER}=1; Path=/; Secure`;
    const fetchMock = stubFetch();
    useSessionStore.getState().markUserLoggedIn(session);

    useSessionStore.getState().invalidateUserSession('deletionScheduled');

    expect(logoutCalls(fetchMock)).toHaveLength(1);
    expect(document.cookie).not.toContain(MARKER);
  });

  it('hands the login screen a reason it has a notice for', () => {
    stubFetch();
    useSessionStore.getState().markUserLoggedIn(session);

    useSessionStore.getState().invalidateUserSession('deletionScheduled');

    expect(useSessionStore.getState().consumeInvalidation()).toBe('deletionScheduled');
  });
});

describe('the reasons that stop short of revoking the cookie', () => {
  it('leaves an admin-removed account its credential to revoke server-side', () => {
    // The discriminator between the two predicates the store now keeps:
    // `accountRemoved` ends the identity — the recents go — but the user did
    // not ask for it, so this client does not spend a request telling the
    // server about a session the server itself just killed.
    seedLocalState();
    document.cookie = `${MARKER}=1; Path=/; Secure`;
    const fetchMock = stubFetch();
    useSessionStore.getState().markUserLoggedIn(session);

    useSessionStore.getState().invalidateUserSession('accountRemoved');

    expect(localStorage.getItem(RECENTS)).toBeNull();
    expect(logoutCalls(fetchMock)).toHaveLength(0);
    expect(document.cookie).toContain(MARKER);
  });

  it('keeps everything when only this client lost its token', () => {
    seedLocalState();
    document.cookie = `${MARKER}=1; Path=/; Secure`;
    const fetchMock = stubFetch();
    useSessionStore.getState().markUserLoggedIn(session);

    useSessionStore.getState().invalidateUserSession('tokenRejected');

    expect(localStorage.getItem(RECENTS)).not.toBeNull();
    expect(logoutCalls(fetchMock)).toHaveLength(0);
    expect(document.cookie).toContain(MARKER);
  });
});

describe('the server closing the socket before the delete response returns', () => {
  // `request_account_deletion` awaits `disconnect_user(4010)` *before* it
  // returns 204, and 4010 on the user axis maps to `accountRemoved`. So the
  // teardown the panel triggers is always the *second* one to arrive, and the
  // idempotence guard used to drop it — leaving a user who scheduled a
  // cancellable deletion reading "removed by an administrator".
  it('still reports the deletion, not the admin revocation', () => {
    useSessionStore.getState().markUserLoggedIn(session);

    useSessionStore.getState().invalidateUserSession('accountRemoved');
    useSessionStore.getState().invalidateUserSession('deletionScheduled');

    expect(useSessionStore.getState().consumeInvalidation()).toBe(
      'deletionScheduled',
    );
  });

  it('does not let a later unrelated teardown overwrite an ordinary one', () => {
    useSessionStore.getState().markUserLoggedIn(session);

    useSessionStore.getState().invalidateUserSession('accountRemoved');
    useSessionStore.getState().invalidateUserSession('tokenRejected');

    expect(useSessionStore.getState().consumeInvalidation()).toBe(
      'accountRemoved',
    );
  });
});

describe('a peer tab that took the same 4010 close', () => {
  // Every open tab holds its own user-events socket and the server closes all
  // of them before the delete response returns, so each independently labels
  // the teardown `accountRemoved`. Only the tab that issued the delete knows
  // better, and it relays that over the session channel.
  async function relayCorrection(): Promise<void> {
    const peer = new BroadcastChannel('mobaide-session');
    peer.postMessage({ type: 'reasonCorrection', reason: 'deletionScheduled' });
    await new Promise((resolve) => {
      setTimeout(resolve, 0);
    });
    peer.close();
  }

  it('is re-labelled when the correction lands after its own teardown', async () => {
    sessionStorage.setItem('mobaide-session', JSON.stringify(session));
    await bootstrapSession();
    useSessionStore.getState().invalidateUserSession('accountRemoved');

    await relayCorrection();

    expect(useSessionStore.getState().consumeInvalidation()).toBe(
      'deletionScheduled',
    );
  });

  it('is re-labelled when the correction lands before its own teardown', async () => {
    sessionStorage.setItem('mobaide-session', JSON.stringify(session));
    await bootstrapSession();

    await relayCorrection();
    useSessionStore.getState().invalidateUserSession('accountRemoved');

    expect(useSessionStore.getState().consumeInvalidation()).toBe(
      'deletionScheduled',
    );
  });

  it('is told about the deletion by the tab that issued it', async () => {
    // The sender half. Without the relay the peers above would never hear
    // anything, so assert the originating tab actually posts the correction —
    // its own local promotion happens either way and proves nothing here.
    sessionStorage.setItem('mobaide-session', JSON.stringify(session));
    await bootstrapSession();
    const seen: unknown[] = [];
    const peer = new BroadcastChannel('mobaide-session');
    peer.onmessage = (event: MessageEvent) => seen.push(event.data);

    // The server's 4010 lands first, then our own delete response settles.
    useSessionStore.getState().invalidateUserSession('accountRemoved');
    useSessionStore.getState().invalidateUserSession('deletionScheduled');
    await new Promise((resolve) => {
      setTimeout(resolve, 0);
    });
    peer.close();

    expect(seen).toContainEqual({
      type: 'reasonCorrection',
      reason: 'deletionScheduled',
    });
  });

  it('leaves a genuine admin revocation alone', async () => {
    sessionStorage.setItem('mobaide-session', JSON.stringify(session));
    await bootstrapSession();

    useSessionStore.getState().invalidateUserSession('accountRemoved');

    expect(useSessionStore.getState().consumeInvalidation()).toBe(
      'accountRemoved',
    );
  });
});

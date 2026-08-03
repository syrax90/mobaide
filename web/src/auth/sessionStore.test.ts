import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { UserSession } from './sessionTransport';

// Node >= 22 defines experimental `localStorage` / `sessionStorage` globals
// that are undefined without --localstorage-file and shadow jsdom's, so
// install functional stand-ins before importing anything that captures them
// (same trap as themeStore.test.ts).
function memoryStorage() {
  const backing = new Map<string, string>();
  return {
    getItem: (key: string) => backing.get(key) ?? null,
    setItem: (key: string, value: string) => {
      backing.set(key, String(value));
    },
    removeItem: (key: string) => {
      backing.delete(key);
    },
    clear: () => {
      backing.clear();
    },
    key: (index: number) => [...backing.keys()][index] ?? null,
    get length() {
      return backing.size;
    },
  };
}

vi.stubGlobal('sessionStorage', memoryStorage());
vi.stubGlobal('localStorage', memoryStorage());

const { bootstrapSession, currentSession, resetSessionBootstrap, useSessionStore } =
  await import('./sessionStore');
const { resetSessionTransport } = await import('./sessionTransport');

const STORAGE_KEY = 'mobaide-session';

function session(overrides: Partial<UserSession> = {}): UserSession {
  return {
    accessToken: 'access-1',
    refreshToken: 'refresh-1',
    userId: 'user-1',
    email: 'user@example.com',
    role: 'viewer',
    scheduledDeletionAt: null,
    ...overrides,
  };
}

function storedSession(): UserSession | null {
  const raw = sessionStorage.getItem(STORAGE_KEY);
  return raw === null ? null : (JSON.parse(raw) as UserSession);
}

beforeEach(() => {
  resetSessionTransport();
  resetSessionBootstrap();
  sessionStorage.clear();
  localStorage.clear();
  useSessionStore.setState({
    authState: { kind: 'unauthenticated' },
    session: null,
    hydrated: false,
    lastInvalidation: null,
  });
});

describe('sessionStore sign-in and sign-out', () => {
  it('adopts a session and persists it for this tab', () => {
    useSessionStore.getState().markUserLoggedIn(session());

    expect(useSessionStore.getState().authState).toEqual({ kind: 'userOnly' });
    expect(currentSession()).toEqual(session());
    expect(storedSession()).toEqual(session());
  });

  it('drops a stale invalidation reason on a fresh sign-in', () => {
    useSessionStore.getState().markUserLoggedIn(session());
    useSessionStore.getState().invalidateUserSession('tokenRejected');

    useSessionStore.getState().markUserLoggedIn(session({ accessToken: 'access-2' }));

    expect(useSessionStore.getState().lastInvalidation).toBeNull();
  });

  it('clears the session and records why on invalidation', () => {
    useSessionStore.getState().markUserLoggedIn(session());

    useSessionStore.getState().invalidateUserSession('tokenRejected');

    const state = useSessionStore.getState();
    expect(state.authState).toEqual({ kind: 'unauthenticated' });
    expect(state.session).toBeNull();
    expect(state.lastInvalidation).toBe('tokenRejected');
    expect(currentSession()).toBeNull();
    expect(storedSession()).toBeNull();
  });

  it('ignores a second invalidation so the reason is not re-emitted', () => {
    // A REST 401 and a WebSocket 4001 close routinely land together on a dead
    // token; the login screen must show its message once, not twice.
    useSessionStore.getState().markUserLoggedIn(session());
    useSessionStore.getState().invalidateUserSession('tokenRejected');
    useSessionStore.getState().consumeInvalidation();

    useSessionStore.getState().invalidateUserSession('accountRemoved');

    expect(useSessionStore.getState().lastInvalidation).toBeNull();
  });

  it('hands the invalidation reason over exactly once', () => {
    useSessionStore.getState().markUserLoggedIn(session());
    useSessionStore.getState().invalidateUserSession('accountRemoved');

    expect(useSessionStore.getState().consumeInvalidation()).toBe('accountRemoved');
    expect(useSessionStore.getState().consumeInvalidation()).toBeNull();
  });

  it('does not resurrect a signed-out session with a late rotation', () => {
    useSessionStore.getState().markUserLoggedIn(session());
    useSessionStore.getState().invalidateUserSession('manual');

    useSessionStore.getState().applyRotatedSession(session({ accessToken: 'access-2' }));

    expect(useSessionStore.getState().session).toBeNull();
    expect(currentSession()).toBeNull();
  });

  it('does not hand a rotation to the account that signed in after it', () => {
    // A refresh that outlived a sign-out followed by a different user signing
    // in would otherwise overwrite the new session with the previous
    // account's token and identity.
    const arrived = session({ userId: 'user-2', email: 'other@example.com' });
    useSessionStore.getState().markUserLoggedIn(session());
    useSessionStore.getState().invalidateUserSession('manual');
    useSessionStore.getState().markUserLoggedIn(arrived);

    const applied = useSessionStore
      .getState()
      .applyRotatedSession(session({ accessToken: 'access-2' }));

    expect(applied).toBe(false);
    expect(useSessionStore.getState().session).toEqual(arrived);
    expect(currentSession()).toEqual(arrived);
    expect(storedSession()).toEqual(arrived);
  });
});

describe('sessionStore incompatible-server takeover', () => {
  it('keeps the takeover on screen when the session is torn down under it', () => {
    useSessionStore.getState().markUserLoggedIn(session());
    useSessionStore
      .getState()
      .markIncompatibleServer({ reason: 'Server requires v8+', minClientVersion: 8 });

    useSessionStore.getState().invalidateUserSession('tokenRejected');

    const state = useSessionStore.getState();
    expect(state.authState.kind).toBe('incompatibleServer');
    expect(state.session).toBeNull();
    expect(state.lastInvalidation).toBe('tokenRejected');
    expect(storedSession()).toBeNull();
  });

  it('returns to the session that survived when the server becomes compatible', () => {
    useSessionStore.getState().markUserLoggedIn(session());
    useSessionStore
      .getState()
      .markIncompatibleServer({ reason: 'Server requires v8+', minClientVersion: 8 });

    useSessionStore.getState().clearIncompatibleServer();

    expect(useSessionStore.getState().authState).toEqual({ kind: 'userOnly' });
  });

  it('returns to signed-out when the session was torn down during the takeover', () => {
    useSessionStore.getState().markUserLoggedIn(session());
    useSessionStore
      .getState()
      .markIncompatibleServer({ reason: 'Server requires v8+', minClientVersion: 8 });
    useSessionStore.getState().invalidateUserSession('tokenRejected');

    useSessionStore.getState().clearIncompatibleServer();

    expect(useSessionStore.getState().authState).toEqual({ kind: 'unauthenticated' });
  });
});

describe('bootstrapSession', () => {
  it('restores the session this tab persisted', async () => {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session()));

    await bootstrapSession();

    const state = useSessionStore.getState();
    expect(state.hydrated).toBe(true);
    expect(state.authState).toEqual({ kind: 'userOnly' });
    expect(state.session).toEqual(session());
  });

  it('settles as signed out when neither storage nor a peer tab answers', async () => {
    await bootstrapSession();

    const state = useSessionStore.getState();
    expect(state.hydrated).toBe(true);
    expect(state.authState).toEqual({ kind: 'unauthenticated' });
    expect(state.session).toBeNull();
  });

  it('does not clobber a takeover that landed while hydration was in flight', async () => {
    // Hydration is async but the version probe is not, so the gate routinely
    // resolves first; writing authState unconditionally here would drop the
    // user back into an app that cannot talk to the server.
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session()));
    const pending = bootstrapSession();

    useSessionStore
      .getState()
      .markIncompatibleServer({ reason: 'Server requires v8+', minClientVersion: 8 });
    await pending;

    const state = useSessionStore.getState();
    expect(state.authState.kind).toBe('incompatibleServer');
    expect(state.session).toEqual(session());
    expect(state.hydrated).toBe(true);
  });

  it('ignores a malformed persisted session rather than sending it as a bearer', async () => {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ accessToken: 42 }));

    await bootstrapSession();

    const state = useSessionStore.getState();
    expect(state.authState).toEqual({ kind: 'unauthenticated' });
    expect(state.session).toBeNull();
  });
});

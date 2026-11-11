import { beforeEach, describe, expect, it, vi } from 'vitest';
import { stubWebStorage } from '../test/localStorageStub';
import type { UserSession } from './sessionTransport';

const { discardDevAutoLogin } = vi.hoisted(() => ({ discardDevAutoLogin: vi.fn() }));
vi.mock('../routes/auth/devLogin', () => ({ discardDevAutoLogin }));

// Before importing anything that captures either global.
stubWebStorage('sessionStorage');
stubWebStorage('localStorage');

const { bootstrapSession, currentSession, resetSessionBootstrap, useSessionStore } =
  await import('./sessionStore');
const { resetSessionTransport } = await import('./sessionTransport');

const STORAGE_KEY = 'mobaide-session';

function session(overrides: Partial<UserSession> = {}): UserSession {
  return {
    accessToken: 'access-1',
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
  discardDevAutoLogin.mockClear();
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

describe('sessionStore recent custom paths', () => {
  const BUCKET = 'recent_custom_paths::agent-1';
  const OTHER_BUCKET = 'recent_custom_paths::agent-2';
  const AGENT_TOKENS = 'mobaide-agent-tokens';

  function seedRecents(): void {
    localStorage.setItem(BUCKET, JSON.stringify(['/srv/app']));
    localStorage.setItem(OTHER_BUCKET, JSON.stringify(['/home/alice/work']));
    localStorage.setItem(AGENT_TOKENS, JSON.stringify({ 'agent-1': 'token' }));
  }

  it('drops every agent bucket when the user signs out', () => {
    // The paths name the user's own workstation, so a second account on this
    // browser must not be offered the first account's directories.
    seedRecents();
    useSessionStore.getState().markUserLoggedIn(session());

    useSessionStore.getState().invalidateUserSession('manual');

    expect(localStorage.getItem(BUCKET)).toBeNull();
    expect(localStorage.getItem(OTHER_BUCKET)).toBeNull();
  });

  it('drops them when a peer tab signs the user out', async () => {
    // localStorage is shared across tabs, so the tab that ran the sign-out
    // sweeps for all of them — but this tab must not be left offering the
    // previous account's directories either.
    seedRecents();
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session()));
    await bootstrapSession();

    const peer = new BroadcastChannel('mobaide-session');
    peer.postMessage({ type: 'logout', reason: 'manual', userId: session().userId });
    // Node delivers BroadcastChannel messages through a MessagePort, which a
    // single setTimeout(0) does not outlast under load: wait for the effect.
    await vi.waitFor(() => {
      expect(useSessionStore.getState().session).toBeNull();
    });
    peer.close();
    expect(localStorage.getItem(BUCKET)).toBeNull();
    expect(localStorage.getItem(OTHER_BUCKET)).toBeNull();
  });

  it('keeps them when a peer tab reports only a rejected token', async () => {
    // The site that decides whether the rule means anything: recents live in
    // `localStorage`, which every tab shares, so an ungated peer handler would
    // delete for the whole browser exactly what the originating tab just took
    // care to keep on a network failure.
    seedRecents();
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session()));
    await bootstrapSession();

    const peer = new BroadcastChannel('mobaide-session');
    peer.postMessage({
      type: 'logout',
      reason: 'tokenRejected',
      userId: session().userId,
    });
    await vi.waitFor(() => {
      expect(useSessionStore.getState().session).toBeNull();
    });
    peer.close();
    expect(localStorage.getItem(BUCKET)).not.toBeNull();
  });

  it('follows a peer sign-out sent by a tab on the previous build', async () => {
    // `origin/main` posts `{type:'logout', reason}` with no `userId` at all.
    // Read raw, `undefined !== null` passes the mismatch guard and the
    // sign-out is silently ignored — leaving a new-build tab authenticated
    // after the user signed out next door, for the whole deploy window.
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session()));
    await bootstrapSession();

    const peer = new BroadcastChannel('mobaide-session');
    peer.postMessage({ type: 'logout', reason: 'manual' });
    await vi.waitFor(() => {
      expect(useSessionStore.getState().session).toBeNull();
    });
    peer.close();
  });

  it('drops them when the account was removed under a takeover', () => {
    // The incompatible-server branch returns early, so it needs its own sweep.
    seedRecents();
    useSessionStore.getState().markUserLoggedIn(session());
    useSessionStore
      .getState()
      .markIncompatibleServer({ reason: 'Server requires v8+', minClientVersion: 8 });

    useSessionStore.getState().invalidateUserSession('accountRemoved');

    expect(localStorage.getItem(BUCKET)).toBeNull();
    expect(localStorage.getItem(OTHER_BUCKET)).toBeNull();
  });

  it('keeps them when the agent disconnects', () => {
    // An agent switch is not an identity change: the recents outlive it even
    // though the agent tokens next to them do not.
    seedRecents();
    useSessionStore.getState().markUserLoggedIn(session());
    useSessionStore.getState().markAgentConnected();

    useSessionStore.getState().agentDisconnect();

    expect(localStorage.getItem(BUCKET)).toBe(JSON.stringify(['/srv/app']));
    expect(localStorage.getItem(OTHER_BUCKET)).toBe(JSON.stringify(['/home/alice/work']));
    expect(localStorage.getItem(AGENT_TOKENS)).toBeNull();
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

describe('the manual-stack dev sign-in', () => {
  async function peerLogout(reason: string) {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session()));
    await bootstrapSession();
    const peer = new BroadcastChannel('mobaide-session');
    peer.postMessage({ type: 'logout', reason, userId: session().userId });
    await new Promise((resolve) => {
      setTimeout(resolve, 0);
    });
    peer.close();
  }

  it('is discarded by a manual sign-out in this tab', () => {
    useSessionStore.getState().markUserLoggedIn(session());
    useSessionStore.getState().invalidateUserSession('manual');
    expect(discardDevAutoLogin).toHaveBeenCalled();
  });

  it('is discarded by a manual sign-out in a peer tab', async () => {
    // Without it this tab mounts /login for the first time and signs itself
    // straight back in, undoing the sign-out next door.
    await peerLogout('manual');
    expect(useSessionStore.getState().session).toBeNull();
    expect(discardDevAutoLogin).toHaveBeenCalled();
  });

  it('survives a peer tab that only lost its token', async () => {
    await peerLogout('tokenRejected');
    expect(discardDevAutoLogin).not.toHaveBeenCalled();
  });
});

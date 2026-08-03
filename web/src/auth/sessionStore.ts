import { create } from 'zustand';
import type { AuthState, IncompatibleServerInfo, InvalidationReason } from './authState';
import { clearAgentTokens } from './agentTokens';
import {
  adoptSession,
  clearSession,
  connectSessionChannel,
  hydrateSession,
  readSession,
  writeSession,
  type UserSession,
} from './sessionTransport';

interface SessionState {
  authState: AuthState;
  session: UserSession | null;
  /** False until `bootstrapSession` settles — the app shows a splash meanwhile. */
  hydrated: boolean;
  /** One-shot: why the last session ended, consumed by the login screen. */
  lastInvalidation: InvalidationReason | null;

  markUserLoggedIn: (session: UserSession) => void;
  markAgentConnected: () => void;
  agentDisconnect: () => void;
  invalidateUserSession: (reason: InvalidationReason) => void;
  markIncompatibleServer: (info: IncompatibleServerInfo) => void;
  clearIncompatibleServer: () => void;
  /** Returns false when the session ended while the refresh was in flight. */
  applyRotatedSession: (session: UserSession) => boolean;
  updateScheduledDeletion: (scheduledDeletionAt: string | null) => void;
  consumeInvalidation: () => InvalidationReason | null;
}

export const useSessionStore = create<SessionState>()((set, get) => ({
  authState: { kind: 'unauthenticated' },
  session: null,
  hydrated: false,
  lastInvalidation: null,

  markUserLoggedIn: (session) => {
    writeSession(session);
    // Drop any stale reason so a fresh sign-in does not immediately surface
    // "your session expired" from the previous one. Android does the same with
    // `_sessionEvents.resetReplayCache()`.
    set({ session, authState: { kind: 'userOnly' }, lastInvalidation: null });
  },

  markAgentConnected: () => {
    if (get().session === null) return;
    set({ authState: { kind: 'fullyConnected' } });
  },

  agentDisconnect: () => {
    clearAgentTokens();
    // Demotes only from fullyConnected; the user session survives.
    if (get().authState.kind === 'fullyConnected') {
      set({ authState: { kind: 'userOnly' } });
    }
  },

  invalidateUserSession: (reason) => {
    const state = get();
    // Idempotent. A REST 401 and a WebSocket 4001 close routinely arrive
    // together on a dead token; without this guard the login screen would show
    // its "session expired" message twice. Android added the same no-op.
    if (state.authState.kind === 'unauthenticated' && state.session === null) {
      return;
    }
    // An incompatible-server takeover outranks a sign-out: the update screen
    // must not be replaced by a login form the user cannot get past.
    if (state.authState.kind === 'incompatibleServer') {
      clearSession(reason);
      clearAgentTokens();
      set({ session: null, lastInvalidation: reason });
      return;
    }
    clearSession(reason);
    clearAgentTokens();
    set({
      session: null,
      authState: { kind: 'unauthenticated' },
      lastInvalidation: reason,
    });
  },

  markIncompatibleServer: (info) => {
    set({ authState: { kind: 'incompatibleServer', info } });
  },

  clearIncompatibleServer: () => {
    if (get().authState.kind !== 'incompatibleServer') return;
    set({
      authState: get().session !== null ? { kind: 'userOnly' } : { kind: 'unauthenticated' },
    });
  },

  applyRotatedSession: (session) => {
    // Rotation only — never a promotion, and never a hand-off to a different
    // account. A refresh that started before the user signed out must not
    // resurrect the session when it lands, in this tab or (via writeSession's
    // broadcast) in any other; and one that outlived a sign-out followed by a
    // different user signing in must not overwrite that new session with the
    // previous account's token and identity. The refresh carries the user id
    // the server read back off the consumed token, so a mismatch is always a
    // stale rotation rather than a legitimate one.
    const active = get().session;
    if (active === null || active.userId !== session.userId) return false;
    writeSession(session);
    set({ session });
    return true;
  },

  updateScheduledDeletion: (scheduledDeletionAt) => {
    const session = get().session;
    if (session === null) return;
    const next = { ...session, scheduledDeletionAt };
    writeSession(next);
    set({ session: next });
  },

  consumeInvalidation: () => {
    const reason = get().lastInvalidation;
    if (reason !== null) set({ lastInvalidation: null });
    return reason;
  },
}));

/**
 * Resolve the session at startup and start listening for peer tabs.
 *
 * Idempotent per page load; `main.tsx` awaits it before rendering so no tab
 * flashes the login screen while a handover is still in flight.
 */
let bootstrap: Promise<void> | null = null;

export function bootstrapSession(): Promise<void> {
  if (bootstrap !== null) return bootstrap;
  bootstrap = (async () => {
    connectSessionChannel((message) => {
      const store = useSessionStore.getState();
      if (message.type === 'logout') {
        // Already broadcast by the originating tab — applying it locally must
        // not echo it back around the channel.
        adoptSession(null);
        clearAgentTokens();
        useSessionStore.setState({
          session: null,
          // Same precedence rule as the local `invalidateUserSession`: an
          // incompatible-server takeover is a hard blocking screen and must
          // outrank a sign-out, or a peer's logout would drop this tab back
          // into the normal router against a server it cannot talk to.
          ...(store.authState.kind === 'incompatibleServer'
            ? {}
            : { authState: { kind: 'unauthenticated' } as const }),
          lastInvalidation: message.reason,
        });
        return;
      }
      if (message.type === 'update') {
        // Same two rules the local `applyRotatedSession` enforces, because a
        // broadcast is just a rotation arriving from another tab.
        //
        // Never a promotion: an unauthenticated tab either never signed in or
        // deliberately signed out, and only `markUserLoggedIn` or the boot
        // handover may sign one in.
        if (store.authState.kind === 'unauthenticated') return;
        // Never a hand-off to a different account: a peer signing in as
        // someone else also broadcasts `update`, and adopting it would
        // silently switch this tab's identity out from under the user.
        if (store.session !== null && store.session.userId !== message.session.userId) {
          return;
        }
        adoptSession(message.session);
        useSessionStore.setState({ session: message.session });
      }
    });

    const session = await hydrateSession();
    // Agent tokens live in localStorage (shared across tabs, re-mintable), the
    // user session in sessionStorage. Closing every tab therefore ends the
    // session while leaving agent credentials on disk — so a boot that finds
    // no session is exactly the moment to drop them.
    if (session === null) clearAgentTokens();
    // Hydration is async (it can wait on a peer tab), so the version probe
    // often resolves first. Writing `authState` unconditionally here would
    // clobber an incompatible-server takeover that already landed and drop the
    // user back into a normal app against a server it cannot talk to. iOS
    // guards the same ordering with its `refreshGeneration` check.
    const takenOver =
      useSessionStore.getState().authState.kind === 'incompatibleServer';
    useSessionStore.setState({
      session,
      ...(takenOver
        ? {}
        : {
            authState:
              session !== null ? { kind: 'userOnly' } : { kind: 'unauthenticated' },
          }),
      hydrated: true,
    });
  })();
  return bootstrap;
}

/** Test seam — lets a test re-run bootstrap against fresh storage. */
export function resetSessionBootstrap(): void {
  bootstrap = null;
}

/** Non-reactive read for module-level consumers (the bearer middleware). */
export function currentSession(): UserSession | null {
  return readSession();
}

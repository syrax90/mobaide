import { create } from 'zustand';
import type { AuthState, IncompatibleServerInfo, InvalidationReason } from './authState';
import { clearAgentTokens } from './agentTokens';
import { clearAllRecentPaths } from '../stores/recentPaths';
import {
  adoptSession,
  broadcastReasonCorrection,
  clearSession,
  clearSignedInMarker,
  connectSessionChannel,
  hydrateSession,
  readSession,
  requestPeerSession,
  writeSession,
  type UserSession,
} from './sessionTransport';
import {
  postLogout,
  restoreSessionFromCookie,
  type RefreshOutcome,
} from './userSessionApi';

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

/**
 * Whether a teardown means the identity is over, or only that this client
 * could not reach the server.
 *
 * The recents name the user's own workstation, so they end with the identity
 * that typed them — and they cannot be re-minted, unlike an agent token. But
 * `tokenRejected` also fires when a refresh merely failed to complete, so
 * treating it as an ending would destroy un-re-mintable paths over a flaky
 * connection. Same rule `bootstrapSession` follows on the boot path: the
 * server's verdict, never the network's mood.
 *
 * Deliberately not consulted by `agentDisconnect`, which is an agent switch
 * rather than an identity change.
 *
 * `deletionScheduled` belongs here for the same reason `manual` does — it is a
 * deliberate, server-confirmed teardown, and it was spelled `manual` until it
 * grew its own login-screen notice. Leaving it out would silently stop purging
 * the recents and agent tokens of an account on its way to being deleted.
 */
function endsIdentity(reason: InvalidationReason): boolean {
  return (
    reason === 'manual' ||
    reason === 'accountRemoved' ||
    reason === 'deletionScheduled'
  );
}

export const useSessionStore = create<SessionState>()((set, get) => ({
  authState: { kind: 'unauthenticated' },
  session: null,
  hydrated: false,
  lastInvalidation: null,

  markUserLoggedIn: (session) => {
    // A correction announced against the previous identity must not survive to
    // mislabel a later, genuine admin revocation of this one.
    pendingDeletionNotice = false;
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
    // A peer announced a self-scheduled deletion before this tab's own 4010
    // landed. `accountRemoved` is the only thing a 4010 produces, so this is
    // that same teardown arriving late — take the truthful name instead.
    if (reason === 'accountRemoved' && pendingDeletionNotice) {
      pendingDeletionNotice = false;
      reason = 'deletionScheduled';
    }
    // Idempotent. A REST 401 and a WebSocket 4001 close routinely arrive
    // together on a dead token; without this guard the login screen would show
    // its "session expired" message twice. Android added the same no-op.
    if (state.authState.kind === 'unauthenticated' && state.session === null) {
      // …with one exception, because the server beats us to our own teardown.
      // `request_account_deletion` closes the user channel with 4010 *before*
      // it returns 204 (auth/service.py), and 4010 on the user axis means
      // `accountRemoved`. So on a self-scheduled deletion that close lands
      // first and this guard would drop the reason we raise a moment later,
      // leaving the user reading "your account was removed by an administrator,
      // sign up to start again" after scheduling a *cancellable* 14-day
      // deletion — the opposite of what they need to know.
      //
      // Recording the reason is safe here: only `DeleteAccountPanel` (and a
      // peer tab relaying it) ever raises this one, and this branch sets no
      // session state, so it cannot double-tear-down. The revoke branch below
      // is skipped on this path, which only leaves the readable signed-in
      // marker behind for one boot — `request_account_deletion` already
      // revoked every refresh token server-side, so no credential outlives it.
      if (reason === 'deletionScheduled') {
        set({ lastInvalidation: reason });
        // Every other tab took its own 4010 and labelled it `accountRemoved`;
        // only this one knows the deletion was self-scheduled and cancellable.
        broadcastReasonCorrection(reason);
      }
      return;
    }
    // Only a deliberate sign-out ends the browser's credential. A
    // `tokenRejected` may be this tab reacting to a token that is already
    // dead, and revoking on it would tear down a cookie that a *different*
    // account's fresh tab in this browser has since installed. Scheduling a
    // deletion is as deliberate as pressing Sign out — and it must revoke, or
    // the refresh cookie survives to restore the session of an account the user
    // just asked us to delete.
    //
    // Above the takeover branch on purpose: signing out from the update screen
    // must revoke like any other sign-out. Dropping the marker locally is what
    // makes it stick when the POST cannot reach the server at all.
    if (reason === 'manual' || reason === 'deletionScheduled') {
      clearSignedInMarker();
      postLogout();
    }
    // An incompatible-server takeover outranks a sign-out: the update screen
    // must not be replaced by a login form the user cannot get past.
    if (state.authState.kind === 'incompatibleServer') {
      clearSession(reason);
      clearAgentTokens();
      if (endsIdentity(reason)) clearAllRecentPaths();
      set({ session: null, lastInvalidation: reason });
      return;
    }
    clearSession(reason);
    clearAgentTokens();
    if (endsIdentity(reason)) clearAllRecentPaths();
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

/**
 * Bumped by a peer `logout`, so a handover still in flight can tell that the
 * session it is about to install has since been ended.
 *
 * `BroadcastChannel` does not order messages from different senders, so with
 * two signed-in peers a `logout` from one and an `offer` from the other can
 * arrive in that order. Without this, the logout handler would clear the tab
 * and the resolving handover would then reinstate the very session that was
 * just signed out — in the store *and* on disk, since `hydrateSession`
 * persists whatever it accepts. Sign-out now revokes the refresh cookie
 * server-side, so the reinstated session could not renew itself; what the
 * guard prevents is the tab presenting a signed-out user with an authenticated
 * UI until that access token expires.
 */
let logoutGeneration = 0;

/**
 * Set when a peer announced a self-scheduled deletion before this tab's own
 * 4010 close had arrived.
 *
 * `BroadcastChannel` does not order messages against this tab's WebSocket, so
 * the correction can land on either side of the teardown it describes. Applied
 * immediately when the teardown is already recorded, and consumed by the next
 * `accountRemoved` otherwise — which is the only reason a 4010 produces.
 */
let pendingDeletionNotice = false;

export function bootstrapSession(): Promise<void> {
  if (bootstrap !== null) return bootstrap;
  bootstrap = (async () => {
    connectSessionChannel((message) => {
      const store = useSessionStore.getState();
      if (message.type === 'reasonCorrection') {
        // Re-label, never re-tear-down: a peer that took the same 4010 has
        // already purged and revoked, and running that again would be a second
        // teardown. Gating on the recorded `accountRemoved` is what keeps this
        // from touching a tab that never took the teardown being corrected.
        if (
          store.authState.kind === 'unauthenticated' &&
          store.lastInvalidation === 'accountRemoved'
        ) {
          useSessionStore.setState({ lastInvalidation: message.reason });
        } else {
          pendingDeletionNotice = true;
        }
        return;
      }
      if (message.type === 'logout') {
        // The same rule the `update` branch below enforces, for the same
        // reason: a sign-out belongs to one account. A stale tab signing out
        // must not tear down a tab that has since signed in as someone else —
        // which would now also mean purging that account's recents and, on
        // the originating side, revoking its cookie.
        if (
          message.userId !== null &&
          store.session !== null &&
          store.session.userId !== message.userId
        ) {
          return;
        }
        logoutGeneration += 1;
        // Already broadcast by the originating tab — applying it locally must
        // not echo it back around the channel.
        adoptSession(null);
        clearAgentTokens();
        // Gated like the local teardown, and this is the site that decides
        // whether the rule means anything: `clearAllRecentPaths` sweeps
        // `localStorage`, which every tab shares, so an ungated peer handler
        // would delete for the whole browser exactly what the originating tab
        // just took care to keep.
        if (endsIdentity(message.reason)) clearAllRecentPaths();
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

    const generation = logoutGeneration;
    const hydrated = await hydrateSession();
    // `restorable` means only that nothing local answered — the refresh cookie
    // still might. This is the leg that makes a session outlive the last tab,
    // and it is why the generation check below has to happen after it rather
    // than after the handover: the window it guards is now a round trip wide,
    // not 300 ms.
    const trustedMarker = hydrated.kind === 'restorable' && hydrated.marker === 'trusted';
    const restored: RefreshOutcome | { kind: 'absent' } =
      hydrated.kind === 'session'
        ? { kind: 'success', session: hydrated.session }
        : hydrated.kind === 'restorable'
          ? await restoreSessionFromCookie()
          : { kind: 'absent' };

    // Reconcile against anything the channel handler did while hydration was
    // in flight — it ran on live messages, so it is more current than the
    // handover in both directions.
    const store = useSessionStore.getState();
    const session =
      // A peer signed out mid-boot: discard the handover rather than
      // reinstating a session the user just ended.
      generation !== logoutGeneration
        ? null
        : // A peer's rotation or sign-in landed mid-boot: keep it rather than
          // overwriting with a handover that resolved to nothing.
          (store.session ?? (restored.kind === 'success' ? restored.session : null));

    // `hydrateSession` may have accepted and persisted an offer that the
    // reconciliation above just rejected, so realign the transport before
    // anything reads a token from it.
    adoptSession(session);
    // Disk state follows the server's verdict, never the network's mood — and
    // `absent` is not a verdict, it is never having asked. Three outcomes:
    //
    //  - `absent`: no credential anywhere to ask about. Behaves exactly as it
    //    did before the cookie existed — drop the agent tokens, which are
    //    stale and re-mintable, and keep the recents, which are neither. This
    //    is also the shape a cookie-blocked browser takes on every boot, so
    //    purging recents here would quietly punish those users.
    //  - `terminal`: the server rejected the cookie, so the identity is over
    //    and the recents that name the user's workstation go with it — but
    //    only on a marker this origin must have written. A sibling host can
    //    plant the unprefixed name, and while it cannot authenticate anything,
    //    it must not be able to trigger a purge on every boot either.
    //  - `transient`: offline. Touch nothing.
    //
    // A peer logout (generation moved) already purged both on its way through.
    let rescued: UserSession | null = null;
    if (session === null && generation === logoutGeneration) {
      if (restored.kind === 'absent') {
        clearAgentTokens();
      } else {
        // Both remaining outcomes ask peers one last time, for the same
        // reason: settling `unauthenticated` beside a live peer strands this
        // tab on the login screen until a manual reload, because an
        // unauthenticated tab drops the `update` broadcast that would
        // otherwise rescue it. `terminal` reaches here when N tabs race the
        // same cookie without Web Locks and all but one see a spurious 401;
        // `transient` reaches here whenever a tab queued behind the others
        // exhausts RESTORE_TIMEOUT_MS — which is the *expected* shape of a
        // mass tab restore, not an edge case.
        rescued = await requestPeerSession();
        if (rescued !== null) {
          adoptSession(rescued);
        } else if (restored.kind === 'terminal') {
          // Only a server verdict destroys anything. A `transient` boot is a
          // user who is offline, and touches no disk state at all.
          clearAgentTokens();
          if (trustedMarker) clearAllRecentPaths();
          // The server has rejected this credential, so the marker is now a
          // standing lie: left alone it would re-enter the restore leg on
          // every future load for the cookie's full 30 days, holding the
          // splash through a round trip that cannot succeed.
          clearSignedInMarker();
        }
      }
    }
    const settled = session ?? rescued;
    // Hydration is async (it can wait on a peer tab, then on the server, then
    // on peers again), so the version probe usually resolves first. Writing
    // `authState` unconditionally here would clobber an incompatible-server
    // takeover that already landed and drop the user back into a normal app
    // against a server it cannot talk to. iOS guards the same ordering with
    // its `refreshGeneration` check. Re-read rather than reusing the snapshot
    // above: several awaits separate them, and a takeover can land in any of
    // those gaps.
    const takenOver =
      useSessionStore.getState().authState.kind === 'incompatibleServer';
    useSessionStore.setState({
      session: settled,
      ...(takenOver
        ? {}
        : {
            authState:
              settled !== null ? { kind: 'userOnly' } : { kind: 'unauthenticated' },
          }),
      hydrated: true,
    });
  })();
  return bootstrap;
}

/** Test seam — lets a test re-run bootstrap against fresh storage. */
export function resetSessionBootstrap(): void {
  bootstrap = null;
  pendingDeletionNotice = false;
}

/** Non-reactive read for module-level consumers (the bearer middleware). */
export function currentSession(): UserSession | null {
  return readSession();
}

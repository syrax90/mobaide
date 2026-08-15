/**
 * Auth state machine, mirroring Android's `SessionManager.AuthState` and iOS's
 * `AuthState` state-for-state so the three clients behave identically.
 *
 * `userOnly` means a central-server user session exists but no agent is
 * connected; `fullyConnected` adds per-agent tokens. `incompatibleServer` is a
 * terminal takeover — only a successful re-probe of `/api/version` clears it.
 */
export type AuthState =
  | { kind: 'unauthenticated' }
  | { kind: 'userOnly' }
  | { kind: 'fullyConnected' }
  | { kind: 'incompatibleServer'; info: IncompatibleServerInfo };

export interface IncompatibleServerInfo {
  reason: string;
  minClientVersion: number;
}

/**
 * Why a user session was torn down. Drives the message the login screen shows
 * on arrival — `manual` is silent because the user asked for it.
 */
export type InvalidationReason =
  | 'tokenRejected'
  | 'accountRemoved'
  | 'deletionScheduled'
  | 'manual';

/**
 * Copy lifted from Android `LoginViewModel`, which collects the same reasons —
 * except `deletionScheduled`, which carries the notice the retired
 * `/portal/login?deleted=1` used to render. Scheduling a deletion signs you out
 * everywhere, so without it the last thing a user sees is a login form with no
 * explanation and no hint that the grace window is the way back.
 */
export const INVALIDATION_MESSAGES: Record<InvalidationReason, string | null> = {
  tokenRejected: 'Your session has expired. Please sign in again.',
  accountRemoved:
    'Your account was removed by an administrator. Sign up to start again or contact support.',
  deletionScheduled:
    'Your account is scheduled for deletion. Sign in again within the grace window to cancel it.',
  manual: null,
};

export function isAuthenticated(state: AuthState): boolean {
  return state.kind === 'userOnly' || state.kind === 'fullyConnected';
}

/**
 * Auto-resume: on entering the sessions list, go straight back into the session
 * you were last using, if it is still alive.
 *
 * Ported from Android `SessionListViewModel.maybeAutoResume` / iOS
 * `SessionListModel.maybeAutoResume`, which are line-for-line ports of each
 * other.
 */
import type { SessionResponse } from '../../protocol/dto/session';

export type AutoResumeDecision =
  | { kind: 'resume'; sessionId: string }
  /** The session is in the list but dead — forget it so it stops being tried. */
  | { kind: 'clear' }
  | { kind: 'none' };

/**
 * Note the asymmetry, which both mobile clients have and their tests pin: an id
 * that is **absent from the list entirely** produces `none`, *not* `clear`. A
 * list that arrived truncated, or from an agent that has not finished
 * enumerating, must not be treated as proof the session is gone.
 */
export function decideAutoResume(
  sessions: readonly SessionResponse[],
  lastUsedSessionId: string | null,
): AutoResumeDecision {
  if (sessions.length === 0 || lastUsedSessionId === null) return { kind: 'none' };
  const match = sessions.find((session) => session.session_id === lastUsedSessionId);
  if (match === undefined) return { kind: 'none' };
  if (match.state === 'running' || match.state === 'starting') {
    return { kind: 'resume', sessionId: lastUsedSessionId };
  }
  return { kind: 'clear' };
}

/**
 * Which agents have already had their one auto-resume attempt this page load.
 *
 * Mobile keeps this flag on the list model, which stays alive underneath the
 * pushed terminal. A web route component unmounts on navigate and mounts again
 * on Back, so a ref or component state would fire a second time and trap the
 * user in a loop between the list and the terminal.
 *
 * Module scope is the right lifetime: it spans route remounts within the SPA but
 * resets on a reload, which is the web analogue of the mobile cold start that
 * legitimately re-attempts. Keyed by agent so switching A → B → A does not
 * re-navigate either.
 */
const attempted = new Set<string>();

/** True at most once per agent per page load, and marks the attempt as spent. */
export function claimAutoResumeAttempt(agentId: string): boolean {
  if (attempted.has(agentId)) return false;
  attempted.add(agentId);
  return true;
}

/** Test seam. */
export function resetAutoResumeAttempts(): void {
  attempted.clear();
}

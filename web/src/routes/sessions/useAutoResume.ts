import { useEffect } from 'react';
import { useNavigate } from 'react-router';
import type { SessionResponse } from '../../protocol/dto/session';
import { readLastUsedSession, useLastSessionStore } from '../../stores/lastSessionStore';
import { claimAutoResumeAttempt, decideAutoResume } from './autoResume';
import { terminalPath } from './terminalPath';

/**
 * Navigate straight back into the last session, once, on entering the list.
 *
 * Armed by the first **non-empty** list, matching both mobile clients: an empty
 * snapshot is what a list looks like before the agent has answered, and burning
 * the attempt on it would mean auto-resume never fires.
 *
 * `synced` is the second half of that guard: a list can also be non-empty
 * because it is left over from a *disconnected* agent, still showing sessions
 * that agent lost when it restarted. Resuming into one of those opens a
 * terminal for a session that no longer exists.
 */
export function useAutoResume(
  agentId: string,
  sessions: readonly SessionResponse[] | undefined,
  synced: boolean,
): void {
  const navigate = useNavigate();
  const clearLastUsedSession = useLastSessionStore((state) => state.clearLastUsedSession);

  useEffect(() => {
    if (sessions === undefined || sessions.length === 0 || !synced) return;
    if (!claimAutoResumeAttempt(agentId)) return;

    const decision = decideAutoResume(sessions, readLastUsedSession(agentId));
    if (decision.kind === 'resume') {
      void navigate(terminalPath(agentId, decision.sessionId));
    } else if (decision.kind === 'clear') {
      clearLastUsedSession(agentId);
    }
  }, [agentId, sessions, synced, navigate, clearLastUsedSession]);
}

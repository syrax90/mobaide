/**
 * Whether the cached session list is a snapshot from the *currently connected*
 * agent (Android `SessionRepository.observeSessionsSynced`, iOS
 * `SessionRepository.sessionsSynced`).
 *
 * Only meaningful for the two decisions that act on a session being **absent**
 * — auto-resume and the terminal's session-gone check. A list left over from a
 * dropped agent channel is not evidence of anything: after an agent restarts it
 * still lists the sessions that died with it, and acting on that is what walked
 * a client into attaching to a session id the agent no longer had.
 *
 * Both halves are required. The snapshot flag alone would stay true across a
 * silent socket drop; the channel status alone would go true the instant the
 * socket opened, before the replayed snapshot reconciled the cache.
 */
import { agentEventsChannelName } from './channels';
import { useConnectionStore } from './connectionStore';
import { useSessionsSyncStore } from './sessionsSyncStore';

export function useSessionsSynced(agentId: string): boolean {
  const name = agentEventsChannelName(agentId);
  const connected = useConnectionStore(
    (state) => state.channels[name]?.status === 'connected',
  );
  const snapshotApplied = useSessionsSyncStore(
    (state) => state.snapshots[agentId] === true,
  );
  return connected && snapshotApplied;
}

/**
 * Whether a full `sessions.changed` snapshot has landed for an agent on its
 * *current* channel connection.
 *
 * The channel being open is not enough on its own. The agent replays its
 * snapshot right after every open, so between the socket opening and that frame
 * arriving the cache still holds the pre-drop list — which, after an agent
 * restart, names sessions the agent no longer has. Anything deciding on a
 * session's **absence** has to wait for the frame, not the socket.
 *
 * Cleared on connect (a new connection has proved nothing yet) and on teardown;
 * set when the snapshot is applied. Mirrors Android
 * `SessionRepository.observeSessionsSynced` and iOS
 * `SessionRepository.sessionsSynced`, both of which flip on the snapshot rather
 * than on the socket.
 */
import { create } from 'zustand';

interface SessionsSyncState {
  /** Agent ids whose current connection has delivered a snapshot. */
  snapshots: Record<string, true>;
  markSnapshotApplied: (agentId: string) => void;
  clearSnapshot: (agentId: string) => void;
}

export const useSessionsSyncStore = create<SessionsSyncState>()((set, get) => ({
  snapshots: {},

  markSnapshotApplied: (agentId) => {
    if (get().snapshots[agentId] === true) return;
    set((state) => ({ snapshots: { ...state.snapshots, [agentId]: true } }));
  },

  clearSnapshot: (agentId) => {
    const { [agentId]: removed, ...rest } = get().snapshots;
    if (removed === undefined) return;
    set({ snapshots: rest });
  },
}));

/** Non-reactive writes, for the channel plumbing outside React. */
export function markSessionsSnapshotApplied(agentId: string): void {
  useSessionsSyncStore.getState().markSnapshotApplied(agentId);
}

export function clearSessionsSnapshot(agentId: string): void {
  useSessionsSyncStore.getState().clearSnapshot(agentId);
}

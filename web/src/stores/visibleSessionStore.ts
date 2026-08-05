import { create } from 'zustand';

interface VisibleSessionState {
  /** The session whose terminal is on screen, or `null`. */
  visibleSessionId: string | null;
  setVisibleSession: (sessionId: string) => void;
  clearVisibleSession: (sessionId: string) => void;
}

/**
 * The web `ForegroundSessionTracker` (both mobile clients have one), and the
 * input to the shared suppression rule: an alert for the session the user is
 * already looking at is noise.
 *
 * Not persisted — it describes this tab right now, and a value restored from a
 * previous visit would suppress the first real alert after a reload.
 *
 * Kept separate from the terminal store's `mountedTerminal`, which answers a
 * different question (which model is exempt from death-driven eviction) and is
 * keyed by channel name rather than session id.
 */
export const useVisibleSessionStore = create<VisibleSessionState>()((set, get) => ({
  visibleSessionId: null,

  setVisibleSession: (sessionId) => {
    set({ visibleSessionId: sessionId });
  },

  /**
   * Clears only if `sessionId` is still the visible one. A route switch mounts
   * the next terminal before the previous one's cleanup runs, so an
   * unconditional clear would blank the successor and re-enable alerts for the
   * session now on screen.
   */
  clearVisibleSession: (sessionId) => {
    if (get().visibleSessionId === sessionId) set({ visibleSessionId: null });
  },
}));

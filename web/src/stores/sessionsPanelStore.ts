import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface SessionsPanelState {
  /**
   * Keep the terminal's sessions panel docked beside the grid instead of
   * sliding over it. Honoured only on a wide viewport; narrower screens always
   * get the overlay, and the flag waits for the window to grow again.
   */
  pinned: boolean;
  setPinned: (value: boolean) => void;
}

export const useSessionsPanelStore = create<SessionsPanelState>()(
  persist(
    (set) => ({
      pinned: false,
      setPinned: (pinned) => {
        set({ pinned });
      },
    }),
    {
      name: 'mobaide-sessions-panel',
      // Same untrusted-boundary rule as `terminalPrefsStore`: anything but a
      // real boolean falls back to the default.
      merge: (persisted, current) => {
        const saved = persisted as Partial<SessionsPanelState> | undefined;
        return {
          ...current,
          pinned: saved?.pinned === true,
        };
      },
    },
  ),
);

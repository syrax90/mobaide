import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface SelectedAgentState {
  /** The agent whose sessions the list shows. Resolved against the live list on load. */
  selectedAgentId: string | null;
  setSelectedAgent: (agentId: string | null) => void;
}

/**
 * Which agent the sessions list is scoped to, remembered across reloads.
 *
 * Deliberately **not** keyed by user: an agent id is server-generated and
 * belongs to exactly one owner, so a second account signing in on this machine
 * cannot match the stored id and simply falls through to the picker's fallback.
 * A stale string on disk is the whole downside.
 */
export const useSelectedAgentStore = create<SelectedAgentState>()(
  persist(
    (set) => ({
      selectedAgentId: null,
      setSelectedAgent: (selectedAgentId) => {
        set({ selectedAgentId });
      },
    }),
    {
      name: 'mobaide-agent',
      // Same untrusted-boundary rule as `themeStore`: localStorage is editable,
      // and a non-string here would reach a query key and a URL.
      merge: (persisted, current) => {
        const stored = (persisted as Partial<SelectedAgentState> | undefined)
          ?.selectedAgentId;
        return {
          ...current,
          selectedAgentId: typeof stored === 'string' && stored !== '' ? stored : null,
        };
      },
    },
  ),
);

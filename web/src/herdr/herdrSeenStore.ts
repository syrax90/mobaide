import { create } from 'zustand';
import { persist } from 'zustand/middleware';

/** Above this the oldest entries go: a pane id is never reused on purpose. */
const MAX_ENTRIES = 500;

interface HerdrSeenState {
  /** `paneSeenKey` → the `turn` the user last saw on this device. */
  seen: Record<string, number>;
  markSeen: (key: string, turn: number) => void;
}

/**
 * Which finished turns the user has already looked at, on this device.
 *
 * Persisted, because a reload must not turn every finished agent back into
 * "Done". Kept on the device rather than in herdr: the only herdr call that
 * marks an agent seen also moves the view of a user at the desktop.
 */
export const useHerdrSeenStore = create<HerdrSeenState>()(
  persist(
    (set, get) => ({
      seen: {},
      markSeen: (key, turn) => {
        const current = get().seen;
        if (current[key] === turn) return;
        const entries = Object.entries({ ...current, [key]: turn });
        set({ seen: Object.fromEntries(entries.slice(-MAX_ENTRIES)) });
      },
    }),
    {
      name: 'mobaide-herdr-seen',
      // Untrusted boundary, as in `terminalPrefsStore`: only real numbers
      // under string keys survive a reload.
      merge: (persisted, current) => {
        const saved = (persisted as { seen?: unknown } | undefined)?.seen;
        const seen: Record<string, number> = {};
        if (typeof saved === 'object' && saved !== null) {
          for (const [key, value] of Object.entries(saved)) {
            if (typeof value === 'number' && Number.isFinite(value)) seen[key] = value;
          }
        }
        return { ...current, seen };
      },
    },
  ),
);

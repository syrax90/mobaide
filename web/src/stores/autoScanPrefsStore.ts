import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface AutoScanPrefsState {
  /**
   * The Auto Scan wizard hides its label tips. One setting per browser; the
   * mobile apps keep their own.
   */
  tipsHidden: boolean;
  setTipsHidden: (value: boolean) => void;
}

export const useAutoScanPrefsStore = create<AutoScanPrefsState>()(
  persist(
    (set) => ({
      tipsHidden: false,
      setTipsHidden: (tipsHidden) => {
        set({ tipsHidden });
      },
    }),
    {
      name: 'mobaide-autoscan',
      // Untrusted storage (see `terminalPrefsStore`): only a real `true` hides the tips.
      merge: (persisted, current) => {
        const saved = persisted as Partial<AutoScanPrefsState> | undefined;
        return { ...current, tipsHidden: saved?.tipsHidden === true };
      },
    },
  ),
);

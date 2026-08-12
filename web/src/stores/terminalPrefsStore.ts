import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface TerminalPrefsState {
  /**
   * Send `Ctrl+V` to the program as `0x16` instead of pasting.
   *
   * Off by default — pasting is what the chord means everywhere else on a
   * desktop — but Claude Code binds `Ctrl+V` for image paste, so the escape
   * hatch has to exist. No mobile counterpart: neither client has a physical
   * keyboard to steal the chord from.
   */
  passCtrlVToTerminal: boolean;
  setPassCtrlVToTerminal: (value: boolean) => void;
}

export const useTerminalPrefsStore = create<TerminalPrefsState>()(
  persist(
    (set) => ({
      passCtrlVToTerminal: false,
      setPassCtrlVToTerminal: (passCtrlVToTerminal) => {
        set({ passCtrlVToTerminal });
      },
    }),
    {
      name: 'mobaide-terminal',
      // Same untrusted-boundary rule as `themeStore`: anything but a real
      // boolean falls back to the default rather than reaching the key handler
      // as a truthy string. Copying whitelisted fields (never spreading
      // `persisted`) is also what silently drops the retired shortcut-bar and
      // recent-chord keys older builds left under this storage key.
      merge: (persisted, current) => {
        const saved = persisted as Partial<TerminalPrefsState> | undefined;
        return {
          ...current,
          passCtrlVToTerminal: saved?.passCtrlVToTerminal === true,
        };
      },
    },
  ),
);

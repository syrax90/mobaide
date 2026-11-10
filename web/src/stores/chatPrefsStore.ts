import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface ChatPrefsState {
  /**
   * The chat timeline shows answers and questions, and folds thinking and
   * tool calls into one "steps" row per run. On by default, like Claude
   * Code's own focus view. One setting per browser, for every chat session;
   * the mobile apps keep their own (`chat_focus_view`).
   */
  focusView: boolean;
  setFocusView: (value: boolean) => void;
}

export const useChatPrefsStore = create<ChatPrefsState>()(
  persist(
    (set) => ({
      focusView: true,
      setFocusView: (focusView) => {
        set({ focusView });
      },
    }),
    {
      name: 'mobaide-chat',
      // Untrusted storage (see `terminalPrefsStore`): only a real `false`
      // turns the default off.
      merge: (persisted, current) => {
        const saved = persisted as Partial<ChatPrefsState> | undefined;
        return { ...current, focusView: saved?.focusView !== false };
      },
    },
  ),
);

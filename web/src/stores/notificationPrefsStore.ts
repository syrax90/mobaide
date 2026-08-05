import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface NotificationPrefsState {
  /**
   * The user's opt-out for attention alerts, in-app toast included.
   *
   * On by default, and independent of the browser permission: permission is the
   * gate on *system* notifications, while this also silences the in-app surface
   * for someone who does not want to be interrupted at all.
   */
  enabled: boolean;
  setEnabled: (value: boolean) => void;
}

export const useNotificationPrefsStore = create<NotificationPrefsState>()(
  persist(
    (set) => ({
      enabled: true,
      setEnabled: (enabled) => {
        set({ enabled });
      },
    }),
    {
      name: 'mobaide-notifications',
      // Same untrusted-boundary rule as `themeStore` and `terminalPrefsStore`:
      // anything but a real boolean falls back to the default. The default here
      // is `true`, so only an explicit `false` can disable.
      merge: (persisted, current) => ({
        ...current,
        enabled: (persisted as Partial<NotificationPrefsState> | undefined)?.enabled !== false,
      }),
    },
  ),
);

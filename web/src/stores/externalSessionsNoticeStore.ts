import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface ExternalSessionsNoticeState {
  /**
   * Whether the user has permanently dismissed the "sessions started outside
   * Mobaide won't appear here" explainer above the sessions list.
   *
   * Device-local, mirroring Android's `external_sessions_notice_dismissed`
   * DataStore key and its iOS `UserDefaults` twin. Not purged at sign-out —
   * like `launchPrefsStore`, and unlike `recentPaths`, this records how the
   * product works rather than anything about the account.
   */
  dismissed: boolean;
  dismiss: () => void;
}

export const useExternalSessionsNoticeStore = create<ExternalSessionsNoticeState>()(
  persist(
    (set) => ({
      dismissed: false,
      dismiss: () => {
        // Never allowed to throw. `persist` calls `localStorage.setItem` on
        // every `set` with no guard of its own, and this runs inside a click
        // handler that still has to hand focus back to the page heading — a
        // refused write would skip that and surface an uncaught error. The
        // in-memory flip lands before `persist` throws, so the notice still
        // goes away for this session; a re-shown explainer after a reload is a
        // cosmetic loss. Same rule, and the same causes, as
        // `recentPaths.writePaths` and `persistLaunchPrefs`.
        try {
          set({ dismissed: true });
        } catch {
          // Storage refused — a partitioned context, a full quota, private mode.
        }
      },
    }),
    {
      name: 'mobaide-external-sessions-notice',
      // Same untrusted-boundary rule as `themeStore` and `notificationPrefsStore`:
      // anything but a real boolean falls back to the default. The default here
      // is `false`, so only an explicit `true` can suppress the notice — a
      // corrupt value shows it again rather than hiding it forever.
      merge: (persisted, current) => ({
        ...current,
        dismissed:
          (persisted as Partial<ExternalSessionsNoticeState> | undefined)?.dismissed === true,
      }),
    },
  ),
);

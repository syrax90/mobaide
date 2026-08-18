import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export type ThemePreference = 'light' | 'dark' | 'system';

const PREFERENCES: readonly ThemePreference[] = ['light', 'dark', 'system'];

function sanitizePreference(value: unknown): ThemePreference {
  return PREFERENCES.includes(value as ThemePreference)
    ? (value as ThemePreference)
    : 'system';
}

interface ThemeState {
  preference: ThemePreference;
  setPreference: (preference: ThemePreference) => void;
}

export const useThemeStore = create<ThemeState>()(
  persist(
    (set) => ({
      preference: 'system',
      setPreference: (preference) => {
        set({ preference });
      },
    }),
    // Mirrored by the pre-paint boot script in web/index.html (key name,
    // persist envelope shape, and value set) — change both together.
    {
      name: 'mobaide-theme',
      // localStorage is an untrusted boundary: an unknown persisted value
      // would otherwise flow into data-theme (no CSS tokens match) and leave
      // the picker with no segment selected. Same whitelist as the boot script.
      merge: (persisted, current) => ({
        ...current,
        preference: sanitizePreference(
          (persisted as Partial<ThemeState> | undefined)?.preference,
        ),
      }),
    },
  ),
);

export function resolveTheme(
  preference: ThemePreference,
  systemPrefersDark: boolean,
): 'light' | 'dark' {
  if (preference === 'system') {
    return systemPrefersDark ? 'dark' : 'light';
  }
  return preference;
}

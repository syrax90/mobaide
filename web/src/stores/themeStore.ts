import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export type ThemePreference = 'light' | 'dark' | 'system';

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
    { name: 'mobaide-theme' },
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

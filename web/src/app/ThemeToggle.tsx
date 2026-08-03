import { useThemeStore, type ThemePreference } from '../stores/themeStore';

const CYCLE: Record<ThemePreference, ThemePreference> = {
  system: 'light',
  light: 'dark',
  dark: 'system',
};

/**
 * Shared by both shells so the preference is reachable while signed out —
 * otherwise the toggle would live only behind the auth guard.
 */
export function ThemeToggle() {
  const preference = useThemeStore((state) => state.preference);
  const setPreference = useThemeStore((state) => state.setPreference);

  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={() => {
        setPreference(CYCLE[preference]);
      }}
    >
      Theme: {preference}
    </button>
  );
}

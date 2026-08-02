import { NavLink, Outlet } from 'react-router';
import { useThemeStore, type ThemePreference } from '../stores/themeStore';

const CYCLE: Record<ThemePreference, ThemePreference> = {
  system: 'light',
  light: 'dark',
  dark: 'system',
};

export function RootLayout() {
  const preference = useThemeStore((state) => state.preference);
  const setPreference = useThemeStore((state) => state.setPreference);

  return (
    <div className="app-shell">
      <header className="app-header">
        <nav className="app-nav" aria-label="Main">
          <NavLink to="/" className="app-brand">
            Mobaide
          </NavLink>
        </nav>
        <button
          type="button"
          className="theme-toggle"
          onClick={() => {
            setPreference(CYCLE[preference]);
          }}
        >
          Theme: {preference}
        </button>
      </header>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}

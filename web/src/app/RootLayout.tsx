import { NavLink, Outlet } from 'react-router';
import { ThemeToggle } from './ThemeToggle';

export function RootLayout() {
  return (
    <div className="app-shell">
      <header className="app-header">
        <nav className="app-nav" aria-label="Main">
          <NavLink to="/" className="app-brand">
            Mobaide
          </NavLink>
          <NavLink to="/agents">Agents</NavLink>
          <NavLink to="/account">Account</NavLink>
          <NavLink to="/devices">Devices</NavLink>
        </nav>
        <ThemeToggle />
      </header>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}

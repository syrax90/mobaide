import { NavLink, Outlet } from 'react-router';
import { ThemeToggle } from './ThemeToggle';

export function RootLayout() {
  return (
    <div className="app-shell">
      <header className="app-header">
        <nav className="app-nav" aria-label="Main">
          <NavLink to="/" className="app-brand">
            {/* Same asset and same single resolution as the portal header:
                pixel art rendered `pixelated`, so a hidpi screen upscales it in
                crisp blocks rather than fetching a finer, smoother source. The
                alt text alone carries the link's accessible name. */}
            <img
              className="brand-mark"
              src="/wordmark.png"
              width={227}
              height={40}
              alt="Mobaide"
            />
          </NavLink>
          <NavLink to="/agents">Agents</NavLink>
          <NavLink to="/projects">Projects</NavLink>
          <NavLink to="/templates">Templates</NavLink>
          <NavLink to="/account">Account</NavLink>
          {/* Devices is deliberately not here: Settings and Account both link
              to it, and a seventh top-level entry buys nothing on a screen
              where the first five are the daily ones. */}
          <NavLink to="/settings">Settings</NavLink>
        </nav>
        <ThemeToggle />
      </header>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}

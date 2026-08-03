import { Outlet } from 'react-router';
import { ThemeToggle } from './ThemeToggle';

/** Centred single-column chrome for the signed-out screens. */
export function AuthLayout() {
  return (
    <div className="auth-shell">
      <header className="auth-shell-header">
        <ThemeToggle />
      </header>
      <main className="auth-main">
        <Outlet />
      </main>
    </div>
  );
}

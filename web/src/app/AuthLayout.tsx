import { Outlet } from 'react-router';
import { ThemeToggle } from './ThemeToggle';

/**
 * Centred single-column chrome for the signed-out screens, and for `/pair`.
 *
 * `/pair` shares it rather than the app shell because it is reached from a link
 * pasted into a fresh tab as often as from inside the app, and because the same
 * screen has to render for a signed-out visitor mid-bounce.
 */
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

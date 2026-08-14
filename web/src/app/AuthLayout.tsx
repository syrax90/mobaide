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
        {/* Unlinked, unlike the portal's brand: every SPA destination bounces a
            signed-out visitor straight back here, and the marketing home lives
            on another origin. Named anyway, like the site's own unlinked footer
            marks: four of the six routes under this shell head their card with
            something other than the app ("Pair an agent", "Check your email"),
            so a decorative alt would leave the brand nameless there. */}
        <img
          className="brand-mark"
          src="/wordmark.png"
          width={227}
          height={40}
          alt="Mobaide"
        />
        <ThemeToggle />
      </header>
      <main className="auth-main">
        <Outlet />
      </main>
    </div>
  );
}

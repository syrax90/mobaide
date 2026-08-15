import { Outlet } from 'react-router';
import {
  ACCOUNT_DELETION_INFO_URL,
  MARKETING_SITE_URL,
  PRIVACY_POLICY_URL,
} from '../routes/settings/supportLinks';
import { ThemeToggle } from './ThemeToggle';

/**
 * Centred single-column chrome for the signed-out screens, and for `/pair`.
 *
 * `/pair` shares it rather than the app shell because it is reached from a link
 * pasted into a fresh tab as often as from inside the app, and because the same
 * screen has to render for a signed-out visitor mid-bounce.
 *
 * The brand and footer are cross-origin on purpose. These screens are where
 * mobaide.com sends every visitor who presses Sign in, so they carry the same
 * three links the marketing shell does — a visitor who arrived here by mistake,
 * or who came looking for the privacy policy or the deletion instructions,
 * would otherwise be stranded on a page with no way out but the back button.
 *
 * `/pair` inherits that chrome while signed in, which the signed-in `RootLayout`
 * deliberately does not carry. Accepted rather than gated: the pairing payload is
 * already stashed in `sessionStorage` by the time this renders, so following a
 * footer link and coming back loses nothing, and a conditional shell would buy
 * one screen's tidiness at the cost of a second rendering mode to reason about.
 */
export function AuthLayout() {
  return (
    <div className="auth-shell">
      <header className="auth-shell-header">
        <a href={MARKETING_SITE_URL} aria-label="Mobaide home">
          {/* The link's `aria-label` names the link; this `alt` names the image
              for anything that reads it separately. Kept non-empty rather than
              decorative because four of the six routes under this shell head
              their card with something other than the app ("Pair an agent",
              "Check your email"), so the mark is the only place the product is
              named on those screens. */}
          <img
            className="brand-mark"
            src="/wordmark.png"
            width={227}
            height={40}
            alt="Mobaide"
          />
        </a>
        <ThemeToggle />
      </header>
      <main className="auth-main">
        <Outlet />
      </main>
      <footer className="auth-shell-footer">
        <nav className="auth-shell-nav" aria-label="Footer">
          {/* Home leaves the SPA on purpose, so it navigates in place. The
              other two are references you consult without abandoning a
              half-filled sign-in form, and `SettingsScreen` already opens these
              same two constants in a new tab. */}
          <a href={MARKETING_SITE_URL}>Home</a>
          <a href={PRIVACY_POLICY_URL} target="_blank" rel="noreferrer">
            Privacy policy
          </a>
          <a href={ACCOUNT_DELETION_INFO_URL} target="_blank" rel="noreferrer">
            Account deletion
          </a>
        </nav>
      </footer>
    </div>
  );
}

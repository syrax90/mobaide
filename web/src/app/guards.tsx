import { useEffect } from 'react';
import {
  Navigate,
  Outlet,
  redirect,
  useLocation,
  useNavigate,
  type MiddlewareFunction,
} from 'react-router';
import { isAuthenticated } from '../auth/authState';
import { useSessionStore } from '../auth/sessionStore';

/**
 * Where an unauthenticated visitor is sent, carrying the path they wanted so
 * sign-in can return them to it (consumed by `useAfterAuth`).
 */
function loginRedirect(url: string): string {
  const target = new URL(url);
  const next = `${target.pathname}${target.search}`;
  return next === '/' ? '/login' : `/login?next=${encodeURIComponent(next)}`;
}

/**
 * Route middleware guarding the authenticated tree.
 *
 * Middleware rather than a loader: in React Router v8 loaders run in parallel,
 * so a parent loader's redirect does not stop children from loading, while a
 * parent middleware short-circuits the whole match. No loaders exist yet — all
 * data comes through TanStack Query — but the later web milestones add them,
 * and this is the shape that stays correct when they do.
 *
 * Middleware only runs on navigation, so it cannot catch a session dying while
 * the user sits on a page. `RequireAuth` below covers that.
 */
export const requireAuthMiddleware: MiddlewareFunction = ({ request }) => {
  if (!isAuthenticated(useSessionStore.getState().authState)) {
    // Throwing the Response that `redirect()` returns is React Router's own
    // control-flow contract for aborting a match, not an error path — hence
    // the exemption from only-throw-error.
    // eslint-disable-next-line @typescript-eslint/only-throw-error
    throw redirect(loginRedirect(request.url));
  }
};

/**
 * Reactive half of the guard: redirect when the session ends mid-page.
 *
 * This is the `LaunchedEffect(authState)` in Android's `AppNavigation` — a
 * background query returning 401 tears the session down without any
 * navigation, and nothing else would move the user off the screen.
 */
export function RequireAuth() {
  const authState = useSessionStore((state) => state.authState);
  const navigate = useNavigate();
  const location = useLocation();
  const authed = isAuthenticated(authState);

  useEffect(() => {
    if (authed) return;
    const next = `${location.pathname}${location.search}`;
    void navigate(
      next === '/' ? '/login' : `/login?next=${encodeURIComponent(next)}`,
      { replace: true },
    );
  }, [authed, location.pathname, location.search, navigate]);

  if (!authed) return null;
  return <Outlet />;
}

/**
 * Where an authenticated user belongs, given the current query string.
 *
 * `next` is attacker-controllable through a crafted link, so sign-in must not
 * become an open redirect. Resolving against the page origin and comparing
 * origins defers to the same URL parser the browser will use, which is the
 * point: hand-rolled string checks miss cases the parser accepts. `/\evil.com`
 * passes a `startsWith('/') && !startsWith('//')` test but parses as the
 * authority `evil.com`, because a backslash is equivalent to a slash in a
 * special scheme.
 */
export function safeNextPath(search: string): string {
  const next = new URLSearchParams(search).get('next');
  // Rooted-path precondition: the guard only ever writes `${pathname}${search}`,
  // so anything else is not a target this app produced. Cheap, and it keeps a
  // bare `evil.example` (same-origin once resolved, but never legitimate) out.
  if (next === null || !next.startsWith('/')) return '/';
  try {
    const url = new URL(next, window.location.origin);
    if (url.origin !== window.location.origin) return '/';
    // The parser resolves dot-segments before serialising, so `..` can eat the
    // leading slash and strand a second one: `/..//evil.com` normalises to the
    // same-origin *pathname* `//evil.com`, which passes the origin check above
    // but is re-read as an authority by `pushState`/`location.assign`.
    if (url.pathname.startsWith('//')) return '/';
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return '/';
  }
}

/**
 * Keeps a signed-in user off the auth screens — landing on /login while
 * already authenticated would otherwise offer a second sign-in.
 *
 * It resolves the same `next` target the sign-in handler does. That agreement
 * is load-bearing: adopting a session re-renders this guard, so it races the
 * handler's own redirect, and if the two disagreed the deep link would be lost
 * whenever this one won.
 */
export function RequireAnonymous() {
  const authState = useSessionStore((state) => state.authState);
  const location = useLocation();
  if (isAuthenticated(authState)) {
    return <Navigate to={safeNextPath(location.search)} replace />;
  }
  return <Outlet />;
}

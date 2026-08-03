import { useEffect } from 'react';
import { RouterProvider } from 'react-router';
import { checkVersionGate } from '../api/bootstrap';
import { useServerInfo } from '../api/useServerInfo';
import { useSessionStore } from '../auth/sessionStore';
import { IncompatibleServer } from './IncompatibleServer';
import { router } from './router';

/**
 * Everything that must resolve before the app can be trusted to render.
 *
 * Two gates, in order:
 *
 *  1. Session hydration. Resolving a session can require a round trip to a
 *     peer tab (see `sessionTransport`), so rendering the router first would
 *     flash the login screen in every newly opened tab of a signed-in user.
 *  2. Protocol version. An incompatible server is a terminal takeover rendered
 *     *outside* the router, mirroring `RootView.swift` and the early return in
 *     Android's `AppNavigation` — a route-level screen could be navigated away
 *     from.
 *
 * A failed version probe is deliberately not a gate failure: an unreachable or
 * slow server must not lock the user out of a client that might still work.
 */
export function AppGate() {
  const hydrated = useSessionStore((state) => state.hydrated);
  const authState = useSessionStore((state) => state.authState);
  const markIncompatibleServer = useSessionStore(
    (state) => state.markIncompatibleServer,
  );
  const clearIncompatibleServer = useSessionStore(
    (state) => state.clearIncompatibleServer,
  );
  const { data: serverInfo } = useServerInfo();

  useEffect(() => {
    if (serverInfo === undefined) return;
    const incompatible = checkVersionGate(serverInfo);
    if (incompatible !== null) {
      markIncompatibleServer(incompatible);
    } else {
      clearIncompatibleServer();
    }
  }, [serverInfo, markIncompatibleServer, clearIncompatibleServer]);

  if (!hydrated) {
    return (
      <div className="splash" role="status" aria-live="polite">
        Loading…
      </div>
    );
  }

  if (authState.kind === 'incompatibleServer') {
    return <IncompatibleServer info={authState.info} />;
  }

  return <RouterProvider router={router} />;
}

import { useEffect, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { isAuthenticated } from '../auth/authState';
import { useSessionStore } from '../auth/sessionStore';
import { startUserEvents, stopAllRealtime } from './channelRegistry';
import { observeResume } from './resumeOnFocus';
import { observeTabVisibility } from './tabVisibility';

/**
 * Binds the user-events channel to the session, mirroring the mobile buses:
 * running while `userOnly` or `fullyConnected`, stopped otherwise.
 *
 * Renders nothing of its own. The effect is safe under StrictMode's
 * double-invoke because the cleanup fully tears the channels down and
 * `startUserEvents` builds a fresh one — React flushes the unmount pass before
 * the mount pass, so the discarded attempt sees `shouldReconnect === false`
 * when its token mint resolves and never constructs a socket.
 *
 * `userId` is in the dependency list so a handover to a different account
 * rebuilds the socket: the token is baked into the URL at connect time, so a
 * channel that outlived its session would keep pushing the previous account's
 * events into the new one's cache.
 */
export function RealtimeProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const authenticated = useSessionStore((state) => isAuthenticated(state.authState));
  const userId = useSessionStore((state) => state.session?.userId ?? null);

  // Independent of the session: the listeners are cheap, and a resume arriving
  // while signed out finds no channels to reconnect.
  useEffect(() => observeResume(), []);
  // Separate from `observeResume`: that one re-opens sockets, this one publishes
  // whether anything on screen still wants their bytes.
  useEffect(() => observeTabVisibility(), []);

  useEffect(() => {
    if (!authenticated || userId === null) return;
    startUserEvents(queryClient);
    // `stopAllRealtime`, not just the user channel: an agent channel opened by
    // a feature milestone would otherwise keep running under the previous
    // session, still authorised (the relay only checks at handshake) and still
    // pushing that account's events into the new one's cache.
    return () => {
      stopAllRealtime();
    };
  }, [authenticated, userId, queryClient]);

  return <>{children}</>;
}

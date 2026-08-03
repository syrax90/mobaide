import { useCallback } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { safeNextPath } from '../../app/guards';
import type { UserSession } from '../../auth/sessionTransport';
import { useSessionStore } from '../../auth/sessionStore';

/**
 * Adopt a fresh session and land the user where they were headed.
 *
 * The guard stores the blocked path in `?next=`, so a deep link survives the
 * detour through sign-in — which is what WEB-08's `/pair` handoff will rely
 * on. The destination is resolved with the same `safeNextPath` that
 * `RequireAnonymous` uses, because adopting the session re-renders that guard
 * and the two redirects race; agreeing on the target makes the race harmless.
 */
export function useAfterAuth(): (session: UserSession) => void {
  const navigate = useNavigate();
  const location = useLocation();
  const markUserLoggedIn = useSessionStore((state) => state.markUserLoggedIn);

  return useCallback(
    (session: UserSession) => {
      const target = safeNextPath(location.search);
      markUserLoggedIn(session);
      void navigate(target, { replace: true });
    },
    [markUserLoggedIn, navigate, location.search],
  );
}

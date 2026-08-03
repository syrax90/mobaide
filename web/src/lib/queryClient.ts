import { QueryCache, QueryClient } from '@tanstack/react-query';
import { isApiError, isAuthError } from '../api/errors';
import { useToastStore } from '../stores/toastStore';

/**
 * Never retry a client error.
 *
 * A 401 is the motivating case — the default `retry` turns one dead token into
 * a burst of identical 401s, each tripping the session-teardown middleware —
 * but every 4xx is equally final: 403 is the `get_non_admin_api_user` admin
 * rejection, 409/422 are deterministic, and retrying a 429 doubles pressure on
 * the in-memory limiter that just asked us to back off (and can extend the
 * user's own cooldown). Only 5xx and network failures are worth a second try.
 */
function shouldRetry(failureCount: number, error: unknown): boolean {
  if (isApiError(error) && error.status >= 400 && error.status < 500) {
    return false;
  }
  return failureCount < 1;
}

export const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: (error) => {
      // Auth failures already produce a teardown and a redirect to login; a
      // toast on top of that is just noise on the way out.
      if (isAuthError(error)) return;
      const message = isApiError(error)
        ? error.message
        : 'Something went wrong. Check your connection and try again.';
      useToastStore.getState().show(message, 'error');
    },
  }),
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: shouldRetry,
      refetchOnWindowFocus: false,
    },
    mutations: {
      // TanStack's own default, restated because it is a deliberate choice
      // here: every mutation in this app is a non-idempotent POST/DELETE
      // against a rate-limited endpoint (register, resend, forgot-password,
      // delete-account, revoke-device). A retried 5xx that actually committed
      // server-side would send a second email or consume a second limiter slot.
      retry: false,
    },
  },
});

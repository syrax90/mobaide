import { useMutation, useQueryClient } from '@tanstack/react-query';
import { stopSession } from '../../api/sessionRepository';
import { isApiError } from '../../api/errors';
import { queryKeys } from '../../lib/queryKeys';
import { useToastStore } from '../../stores/toastStore';
import { evictTerminals } from '../../terminal/terminalEviction';

interface StopSessionOptions {
  /** Runs before the terminal is evicted, so a caller can leave its view first. */
  onStopped?: (sessionId: string) => void;
  /** Success or failure, before any network reconciliation settles. */
  onSettled?: () => void;
}

/** The Stop / Force Stop mutation shared by the sessions list and the terminal's panel. */
export function useStopSession(agentId: string, { onStopped, onSettled }: StopSessionOptions = {}) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ sessionId, force }: { sessionId: string; force: boolean }) =>
      stopSession(agentId, sessionId, force),
    onSuccess: async (_result, { sessionId }) => {
      onSettled?.();
      onStopped?.(sessionId);
      // Before the network settles: this is what actually kills a live PTY view.
      evictTerminals(agentId, [sessionId]);
      // The agent publishes `sessions.changed` immediately, which is the honest
      // source; this only covers the socket being down.
      await queryClient.invalidateQueries({ queryKey: queryKeys.sessions(agentId) });
    },
    onError: (error, { sessionId }) => {
      onSettled?.();
      // A 404 means the session is already gone — the user's intent is
      // satisfied, so reconcile rather than report a failure. Without this the
      // dead row stays on screen (`staleTime: Infinity` never refetches it) and
      // pressing Stop again just 404s. An unreachable agent answers 503 from the
      // relay, not 404, so this cannot swallow a real connectivity failure.
      if (isApiError(error) && error.status === 404) {
        onStopped?.(sessionId);
        evictTerminals(agentId, [sessionId]);
        void queryClient.invalidateQueries({ queryKey: queryKeys.sessions(agentId) });
        return;
      }
      useToastStore
        .getState()
        .show(
          isApiError(error)
            ? `Failed to stop session: ${error.message}`
            : 'Failed to stop session',
          'error',
        );
    },
  });
}

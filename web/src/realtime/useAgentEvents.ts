/**
 * Keep the agent-events channel open for as long as a screen needs it.
 *
 * Two effects, and they are not redundant:
 *
 * **Ownership** opens the channel and — crucially — closes it again when the
 * selected agent changes or the screen unmounts. Without a cleanup, switching
 * agents would leave the old socket reconnecting forever.
 *
 * **Revival** exists because `RealtimeProvider` calls `stopAllRealtime()` on
 * every auth-session change, closing agent channels out from under components
 * that are still mounted (`channelRegistry.ts` documents this as the contract
 * for whoever owns an agent channel). `tearDown` calls `forgetChannel`, so the
 * connection-store entry disappearing *is* the teardown signal — a more direct
 * observation than re-deriving it from `userId`, and it also covers a teardown
 * from any other cause.
 *
 * Revival must not have a cleanup of its own: disconnect → `forgetChannel` →
 * "not registered" → reconnect is an oscillation. Splitting the two roles is
 * what makes that impossible — revival only ever connects, and its re-run when
 * the channel reappears immediately returns.
 */
import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { isAuthenticated } from '../auth/authState';
import { useSessionStore } from '../auth/sessionStore';
import { agentEventsChannelName } from './channels';
import { connectAgentEvents, disconnectAgentEvents } from './channelRegistry';
import { useConnectionStore } from './connectionStore';

export function useAgentEvents(agentId: string): void {
  const queryClient = useQueryClient();
  const authenticated = useSessionStore((state) => isAuthenticated(state.authState));
  const name = agentEventsChannelName(agentId);
  const registered = useConnectionStore((state) => state.channels[name] !== undefined);

  useEffect(() => {
    if (!authenticated) return;
    connectAgentEvents(queryClient, agentId);
    return () => {
      disconnectAgentEvents(agentId);
    };
  }, [agentId, authenticated, queryClient]);

  useEffect(() => {
    if (!authenticated || registered) return;
    connectAgentEvents(queryClient, agentId);
  }, [agentId, authenticated, registered, queryClient]);
}

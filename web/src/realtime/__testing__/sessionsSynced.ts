/**
 * Shared staging for the "sessions list is agent-authoritative" signal.
 *
 * Test-only, same rule as `fakeSocket.ts`: nothing under `src/` outside a
 * `*.test.ts(x)` may import it. It exists for the same reason — three tests
 * (`useSessionsSynced`, `useSessionGone`, `SessionsScreen`) need to stage both
 * halves of that signal by hand, and the copies had already picked up two names
 * for one concept.
 *
 * Deep imports rather than the barrel, so a test that mocks a module the barrel
 * pulls in does not race its own `vi.mock` factory.
 */
import { agentEventsChannelName } from '../channels';
import { useConnectionStore } from '../connectionStore';
import { markSessionsSnapshotApplied, useSessionsSyncStore } from '../sessionsSyncStore';

/** Both halves: the channel is open *and* has delivered a snapshot. */
export function markSessionsSynced(agentId: string): void {
  useConnectionStore.setState({
    channels: {
      [agentEventsChannelName(agentId)]: {
        status: 'connected',
        attempt: 0,
        lastCloseCode: null,
      },
    },
  });
  markSessionsSnapshotApplied(agentId);
}

/**
 * Both stores are module-scoped singletons, so a test that stages the signal has
 * to hand it back.
 */
export function resetSessionsSynced(): void {
  useConnectionStore.setState({ channels: {} });
  useSessionsSyncStore.setState({ snapshots: {} });
}

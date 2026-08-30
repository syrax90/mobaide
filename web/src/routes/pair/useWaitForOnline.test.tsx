import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Agent } from '../../api/agentRepository';
import { WAIT_FOR_ONLINE_TIMEOUT_SECONDS } from './pairCopy';
import { useWaitForOnline } from './useWaitForOnline';

type Resolve = (agents: Agent[]) => void;

const api = vi.hoisted(
  (): {
    agents: Agent[];
    /** While set, every list fetch hangs until the test resolves it. */
    pending: Resolve[] | null;
    failWith: Error | null;
    calls: number;
  } => ({ agents: [], pending: null, failWith: null, calls: 0 }),
);

vi.mock('../../api/agentRepository', () => ({
  listAgents: (): Promise<Agent[]> => {
    api.calls += 1;
    if (api.failWith !== null) return Promise.reject(api.failWith);
    const queue = api.pending;
    if (queue !== null) {
      return new Promise<Agent[]>((resolve) => {
        queue.push(resolve);
      });
    }
    return Promise.resolve(api.agents);
  },
}));

const agent = (online: boolean): Agent => ({
  agentId: 'agent-1',
  name: 'Workstation',
  online,
  createdAt: '2026-08-04T10:00:00Z',
  lastSeenAt: null,
  agentVersion: null,
  agentProtocolVersion: null,
});

const DEADLINE_MS = WAIT_FOR_ONLINE_TIMEOUT_SECONDS * 1000;

let client: QueryClient;

const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);

const setup = (attempt = 0) =>
  renderHook(({ id, n }: { id: string | null; n: number }) => useWaitForOnline(id, n), {
    wrapper,
    initialProps: { id: 'agent-1', n: attempt },
  });

const advance = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

/** The initial fetch and the one-shot refetch have both landed. */
const settled = () =>
  waitFor(() => {
    expect(client.getQueryState(['agents'])?.fetchStatus).toBe('idle');
  });

/** Release every hung fetch with the same body. */
const resolvePending = (agents: Agent[]) =>
  act(async () => {
    const queue = api.pending ?? [];
    api.pending = null;
    queue.forEach((resolve) => {
      resolve(agents);
    });
    await Promise.resolve();
  });

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  api.agents = [agent(false)];
  api.pending = null;
  api.failWith = null;
  api.calls = 0;
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  client.clear();
});

describe('useWaitForOnline deadline verdict', () => {
  it('holds the timeout until the deadline refetch has settled', async () => {
    const { result } = setup();
    await settled();
    api.pending = [];

    await advance(DEADLINE_MS + 500);

    // The clock has run out, but the verdict is not in until the relay
    // answers — a stale "offline" from the cache must not end the wait.
    expect(result.current.elapsedSeconds).toBe(WAIT_FOR_ONLINE_TIMEOUT_SECONDS);
    expect(result.current.timedOut).toBe(false);
    expect(api.pending.length).toBeGreaterThan(0);

    await resolvePending([agent(false)]);

    await waitFor(() => {
      expect(result.current.timedOut).toBe(true);
    });
    expect(result.current.online).toBe(false);
  });

  it('succeeds when the deadline refetch finds the agent online', async () => {
    const { result } = setup();
    await settled();
    api.pending = [];
    await advance(DEADLINE_MS + 500);
    expect(result.current.timedOut).toBe(false);

    await resolvePending([agent(true)]);

    await waitFor(() => {
      expect(result.current.online).toBe(true);
    });
    expect(result.current.timedOut).toBe(false);
  });

  it('still reports the timeout when the deadline refetch fails', async () => {
    // `refetchQueries` settles either way; a relay that cannot be reached
    // must end the wait rather than strand the screen on "Waiting".
    const { result } = setup();
    await settled();
    api.failWith = new Error('relay unreachable');

    await advance(DEADLINE_MS + 500);

    await waitFor(() => {
      expect(result.current.timedOut).toBe(true);
    });
    expect(result.current.online).toBe(false);
  });
});

describe('useWaitForOnline attempts', () => {
  it('restarts the clock and the verdict for a new attempt on the same agent', async () => {
    const { result, rerender } = setup(0);
    await settled();
    await advance(DEADLINE_MS + 500);
    await waitFor(() => {
      expect(result.current.timedOut).toBe(true);
    });
    const callsBeforeRestart = api.calls;

    rerender({ id: 'agent-1', n: 1 });

    expect(result.current.elapsedSeconds).toBe(0);
    expect(result.current.timedOut).toBe(false);
    // "Keep waiting" gets its own one-shot resync, like a fresh claim.
    await waitFor(() => {
      expect(api.calls).toBeGreaterThan(callsBeforeRestart);
    });

    await advance(DEADLINE_MS / 2);
    expect(result.current.elapsedSeconds).toBe(WAIT_FOR_ONLINE_TIMEOUT_SECONDS / 2);
    expect(result.current.timedOut).toBe(false);

    await advance(DEADLINE_MS / 2 + 500);
    await waitFor(() => {
      expect(result.current.timedOut).toBe(true);
    });
  });
});

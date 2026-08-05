import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionResponse } from '../../protocol/dto/session';

// No agent token means no URL, so no socket is ever constructed — the seam
// `TerminalScreen.test.tsx` uses. What the hook decides is the point here, not
// how the list reaches it.
vi.mock('../../auth/agentTokens', () => ({
  ensureAgentToken: () => Promise.resolve(null),
  clearAgentToken: () => undefined,
}));

const api = vi.hoisted(() => ({
  sessions: [] as SessionResponse[],
  /** Set by {@link holdList} to keep a fetch in flight. */
  gate: null as { promise: Promise<void>; open: () => void } | null,
}));

vi.mock('../../api/sessionRepository', () => ({
  listSessions: async () => {
    await api.gate?.promise;
    return api.sessions;
  },
}));

/** Keep the next fetch pending; the returned callback lets it resolve. */
function holdList(): () => void {
  let open = (): void => undefined;
  const promise = new Promise<void>((resolve) => {
    open = resolve;
  });
  api.gate = { promise, open };
  return () => {
    api.gate = null;
    open();
  };
}

const { sessionResponseSchema } = await import('../../protocol/dto/session');
const { queryKeys } = await import('../../lib/queryKeys');
const { markSessionsSynced, resetSessionsSynced } = await import(
  '../../realtime/__testing__/sessionsSynced'
);
const { useToastStore } = await import('../../stores/toastStore');
const { useSessionGone } = await import('./useSessionGone');

const session = (sessionId: string): SessionResponse =>
  sessionResponseSchema.parse({
    session_id: sessionId,
    state: 'running',
    created_at: '2026-08-04T10:00:00Z',
  });

let client: QueryClient;

function Harness() {
  useSessionGone('a1', 's1');
  return <span data-testid="location">{useLocation().pathname}</span>;
}

function renderHarness() {
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/agents/a1/sessions/s1']}>
        <Harness />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const currentPath = (): string | null => screen.getByTestId('location').textContent;

const markSynced = () => markSessionsSynced('a1');

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  api.sessions = [];
});

afterEach(() => {
  cleanup();
  client.clear();
  resetSessionsSynced();
  useToastStore.setState({ toasts: [] });
});

describe('useSessionGone', () => {
  it('leaves for the sessions list when a synced snapshot has no such session', async () => {
    markSynced();
    client.setQueryData(queryKeys.sessions('a1'), []);

    renderHarness();

    await waitFor(() => {
      expect(currentPath()).toBe('/');
    });
    expect(useToastStore.getState().toasts.map((toast) => toast.message)).toEqual([
      'That session has ended.',
    ]);
  });

  it('stays put while the list is not agent-authoritative', async () => {
    // The exact list of the "gone" case, but nothing has proved it came from
    // the agent we are attached to now: after an agent restart the cache still
    // names the sessions that died with it, and this one may be alive.
    client.setQueryData(queryKeys.sessions('a1'), []);

    renderHarness();

    await waitFor(() => {
      expect(currentPath()).toBe('/agents/a1/sessions/s1');
    });
    expect(useToastStore.getState().toasts).toEqual([]);
  });

  it('waits for a list rather than deciding on no list at all', async () => {
    // `undefined` is "not fetched yet", not "not in the list" — deciding on it
    // would eject the user before the first response lands. The fetch is held
    // open because its answer here is the empty list, which *is* the eject
    // condition: unheld, this would only be measuring whether the first
    // `waitFor` poll beat the response.
    const release = holdList();
    markSynced();

    renderHarness();

    await waitFor(() => {
      expect(client.getQueryState(queryKeys.sessions('a1'))?.fetchStatus).toBe(
        'fetching',
      );
    });
    expect(currentPath()).toBe('/agents/a1/sessions/s1');

    // And the same empty list, once it is actually a list, does eject — so the
    // assertion above is about the missing data, not a hook that never fires.
    release();

    await waitFor(() => {
      expect(currentPath()).toBe('/');
    });
  });

  it('keeps the terminal on screen when the session ends under the user', async () => {
    markSynced();
    client.setQueryData(queryKeys.sessions('a1'), [session('s1')]);

    renderHarness();
    await waitFor(() => {
      expect(currentPath()).toBe('/agents/a1/sessions/s1');
    });

    act(() => {
      client.setQueryData(queryKeys.sessions('a1'), []);
    });

    expect(currentPath()).toBe('/agents/a1/sessions/s1');
    expect(useToastStore.getState().toasts).toEqual([]);
  });
});

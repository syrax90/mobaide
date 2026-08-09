import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionResponse } from '../../protocol/dto/session';

const api = vi.hoisted(() => ({ sessions: [] as SessionResponse[] }));

vi.mock('../../api/sessionRepository', () => ({
  listSessions: () => Promise.resolve(api.sessions),
}));

vi.mock('../../api/customAgentTypeRepository', () => ({
  listCustomAgentTypes: () => Promise.resolve([]),
}));

const { sessionResponseSchema } = await import('../../protocol/dto/session');
const { queryKeys } = await import('../../lib/queryKeys');
const { markSessionsSynced, resetSessionsSynced } = await import(
  '../../realtime/__testing__/sessionsSynced'
);
const { useSessionHeaderLabel } = await import('./useSessionHeaderLabel');

const session = (over: Record<string, unknown> = {}): SessionResponse =>
  sessionResponseSchema.parse({
    session_id: 's1',
    state: 'running',
    created_at: '2026-08-04T10:00:00Z',
    ...over,
  });

let client: QueryClient;

function Harness({ agentId, sessionId }: { agentId: string; sessionId: string }) {
  return <span data-testid="label">{useSessionHeaderLabel(agentId, sessionId)}</span>;
}

function renderHarness(agentId = 'a1', sessionId = 's1') {
  return render(
    <QueryClientProvider client={client}>
      <Harness agentId={agentId} sessionId={sessionId} />
    </QueryClientProvider>,
  );
}

const label = (): string | null => screen.getByTestId('label').textContent;

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  api.sessions = [];
});

afterEach(() => {
  cleanup();
  client.clear();
  resetSessionsSynced();
});

describe('useSessionHeaderLabel', () => {
  it('names an untitled session by its kind, never by its id', async () => {
    markSessionsSynced('a1');
    client.setQueryData(queryKeys.sessions('a1'), [session({ kind: 'codex' })]);

    renderHarness();

    await waitFor(() => {
      expect(label()).toBe('Codex');
    });
  });

  it('switches to the title the agent pushes', async () => {
    markSessionsSynced('a1');
    client.setQueryData(queryKeys.sessions('a1'), [session()]);
    renderHarness();
    await waitFor(() => {
      expect(label()).toBe('Claude Code');
    });

    await act(async () => {
      client.setQueryData(queryKeys.sessions('a1'), [session({ title: '* Fix the header' })]);
    });

    expect(label()).toBe('* Fix the header');
  });

  it('keeps the last label when the session leaves the snapshot', async () => {
    // The terminal deliberately stays readable after a session ends; blanking
    // the bar under the user would be the only visible change.
    markSessionsSynced('a1');
    client.setQueryData(queryKeys.sessions('a1'), [session({ title: '* Fix the header' })]);
    renderHarness();
    await waitFor(() => {
      expect(label()).toBe('* Fix the header');
    });

    // Awaited so the empty list has actually reached the hook — the previous
    // case proves an update lands inside one `act`, so this is not vacuous.
    await act(async () => {
      client.setQueryData(queryKeys.sessions('a1'), []);
    });

    expect(label()).toBe('* Fix the header');
  });

  it('does not carry a label across a route change', async () => {
    // A notification tap navigates terminal→terminal, swapping this route's
    // params without remounting. Naming the incoming session after the one the
    // user just left would be wrong for as long as the new agent stays unsynced.
    markSessionsSynced('a1');
    client.setQueryData(queryKeys.sessions('a1'), [session({ title: '* Fix the header' })]);
    const { rerender } = renderHarness();
    await waitFor(() => {
      expect(label()).toBe('* Fix the header');
    });

    rerender(
      <QueryClientProvider client={client}>
        <Harness agentId="a2" sessionId="s2" />
      </QueryClientProvider>,
    );

    expect(label()).toBe('');
  });
});

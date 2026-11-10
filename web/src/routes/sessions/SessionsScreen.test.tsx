import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubLocalStorage } from '../../test/localStorageStub';
import type { Agent } from '../../api/agentRepository';
import type { SessionResponse } from '../../protocol/dto/session';

// No agent token means no URL, so no socket is ever constructed — the same seam
// `TerminalScreen.test.tsx` uses. The realtime layer is not what these tests
// are about; the tree the screen builds around the list is.
vi.mock('../../auth/agentTokens', () => ({
  ensureAgentToken: () => Promise.resolve(null),
  clearAgentToken: () => undefined,
}));

const api = vi.hoisted(() => ({
  agents: [] as Agent[],
  sessions: [] as SessionResponse[],
  stops: [] as { sessionId: string; force: boolean }[],
}));

vi.mock('../../api/agentRepository', () => ({
  listAgents: () => Promise.resolve(api.agents),
}));

vi.mock('../../api/sessionRepository', () => ({
  listSessions: () => Promise.resolve(api.sessions),
  stopSession: (_agentId: string, sessionId: string, force: boolean) => {
    api.stops.push({ sessionId, force });
    return Promise.resolve();
  },
}));

vi.mock('../../api/customAgentTypeRepository', () => ({
  listCustomAgentTypes: () => Promise.resolve([]),
}));

// Before any persisted store is imported.
stubLocalStorage();

const { sessionResponseSchema } = await import('../../protocol/dto/session');
const { useLastSessionStore } = await import('../../stores/lastSessionStore');
const { useSelectedAgentStore } = await import('../../stores/selectedAgentStore');
const { stopAllRealtime } = await import('../../realtime');
const { resetAutoResumeAttempts } = await import('./autoResume');
const { SessionsScreen } = await import('./SessionsScreen');
// Auto-resume only acts on a list the connected agent just pushed, and no socket
// is ever built here (see the agent-token mock), so both halves of that signal
// are staged directly.
const { markSessionsSynced, resetSessionsSynced } = await import(
  '../../realtime/__testing__/sessionsSynced'
);

const agent = (agentId: string, online: boolean): Agent => ({
  agentId,
  name: agentId,
  online,
  createdAt: '2026-08-04T10:00:00Z',
  lastSeenAt: null,
  agentVersion: null,
  agentProtocolVersion: null,
  supportsUninstall: false,
});

const session = (sessionId: string, over: Record<string, unknown> = {}): SessionResponse =>
  sessionResponseSchema.parse({
    session_id: sessionId,
    state: 'running',
    created_at: '2026-08-04T10:00:00Z',
    ...over,
  });

function Probe() {
  return <span data-testid="location">{useLocation().pathname}</span>;
}

/** Exact, not `toHaveTextContent`: every terminal path contains "/" as a substring. */
const currentPath = (): string | null => screen.getByTestId('location').textContent;

let client: QueryClient;

/** A fresh route tree over the same cache — a Back navigation, not a reload. */
function renderScreen() {
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/']}>
        <Probe />
        <SessionsScreen />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  api.agents = [agent('agent-1', true)];
  api.sessions = [];
  api.stops.length = 0;
  useSelectedAgentStore.setState({ selectedAgentId: 'agent-1' });
});

afterEach(() => {
  cleanup();
  stopAllRealtime();
  resetAutoResumeAttempts();
  useLastSessionStore.setState({ lastUsedSessionIds: {} });
  useSelectedAgentStore.setState({ selectedAgentId: null });
  resetSessionsSynced();
  client.clear();
  localStorage.clear();
});

describe('SessionsScreen', () => {
  it('resumes the last session once, and not again when the user comes back', async () => {
    api.sessions = [session('s1')];
    useLastSessionStore.setState({ lastUsedSessionIds: { 'agent-1': 's1' } });
    markSessionsSynced('agent-1');

    const first = renderScreen();
    await waitFor(() => {
      expect(currentPath()).toBe('/agents/agent-1/sessions/s1');
    });
    first.unmount();

    // Back: the route mounts again with the same session still running. The
    // module-scoped once-flag is the only thing stopping a navigation loop.
    renderScreen();
    await waitFor(() => {
      expect(screen.getByRole('link', { name: /Claude Code/ })).toBeInTheDocument();
    });
    expect(currentPath()).toBe('/');
  });

  it('forgets a last session that is no longer alive instead of resuming it', async () => {
    api.sessions = [session('s1', { state: 'stopped' })];
    useLastSessionStore.setState({ lastUsedSessionIds: { 'agent-1': 's1' } });
    markSessionsSynced('agent-1');

    renderScreen();

    await waitFor(() => {
      expect(useLastSessionStore.getState().lastUsedSessionIds).toEqual({});
    });
    expect(currentPath()).toBe('/');
  });

  it('renders a card per session with its kind label and attention chip', async () => {
    api.sessions = [
      session('s1', { kind: 'shell', title: 'build', cwd: '/srv/mobaide' }),
      session('s2', { kind: 'gemini', attention: 'awaiting_permission' }),
    ];

    renderScreen();

    expect(await screen.findByText('build')).toBeInTheDocument();
    expect(screen.getByText('Terminal')).toBeInTheDocument();
    expect(screen.getByText('Custom · s2')).toBeInTheDocument();
    expect(screen.getByText('Needs permission')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /build/ })).toHaveAttribute(
      'href',
      '/agents/agent-1/sessions/s1',
    );
  });

  it('passes the force flag from the confirm dialog through to the stop call', async () => {
    api.sessions = [session('s1', { title: 'build' })];

    renderScreen();
    fireEvent.click(await screen.findByRole('button', { name: 'Stop' }));
    fireEvent.click(screen.getByRole('button', { name: 'Force Stop' }));

    await waitFor(() => {
      expect(api.stops).toEqual([{ sessionId: 's1', force: true }]);
    });
  });

  it('says the agent is offline rather than loading a query it disabled', async () => {
    // A disabled query with a cold cache reports pending forever, so the
    // offline branch has to be checked first.
    api.agents = [agent('agent-1', false)];

    renderScreen();

    expect(await screen.findByText('This agent is offline.')).toBeInTheDocument();
    expect(screen.queryByText('Loading sessions…')).not.toBeInTheDocument();
  });

  it('points an account with no agents at the pairing route', async () => {
    // Pairing used to live only in the mobile apps; WEB-08 makes the empty
    // state the entry point, so the link is the assertion — a browser user
    // with no agent has nowhere else to start.
    api.agents = [];

    renderScreen();

    expect(await screen.findByText(/No agents yet/)).toBeInTheDocument();
    // `/agents`, not `/pair`: an account with nothing paired has no code to
    // paste yet, and the setup guide is where the install command lives.
    expect(screen.getByRole('link', { name: 'Set up an agent' })).toHaveAttribute(
      'href',
      '/agents',
    );
  });
});

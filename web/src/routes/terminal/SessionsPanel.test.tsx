import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, useLocation, useNavigationType } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubLocalStorage } from '../../test/localStorageStub';
import type { Agent } from '../../api/agentRepository';
import type { SessionResponse } from '../../protocol/dto/session';

const api = vi.hoisted(() => ({
  agents: [] as Agent[],
  sessions: [] as SessionResponse[],
}));

vi.mock('../../api/agentRepository', () => ({
  listAgents: () => Promise.resolve(api.agents),
}));

vi.mock('../../api/sessionRepository', () => ({
  listSessions: () => Promise.resolve(api.sessions),
}));

vi.mock('../../api/customAgentTypeRepository', () => ({
  listCustomAgentTypes: () =>
    Promise.resolve([
      {
        id: 'ab12cd34ef56',
        name: 'Aider',
        command: 'aider',
        created_at: 'c',
        updated_at: 'u',
      },
    ]),
}));

// Before any persisted store is imported.
stubLocalStorage();

const { sessionResponseSchema } = await import('../../protocol/dto/session');
const { useLastSessionStore } = await import('../../stores/lastSessionStore');
const { useSelectedAgentStore } = await import('../../stores/selectedAgentStore');
const { SessionsPanel } = await import('./SessionsPanel');
// No socket is ever built here, so both halves of the synced signal are
// staged directly, the way `TerminalScreen.test.tsx` does.
const { markSessionsSynced, resetSessionsSynced } = await import(
  '../../realtime/__testing__/sessionsSynced'
);

const agent = (agentId: string, name: string, online: boolean): Agent => ({
  agentId,
  name,
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

/** Where the router is and how it got there: a switch must `replace`. */
function Probe() {
  return (
    <>
      <span data-testid="location">{useLocation().pathname}</span>
      <span data-testid="navigation-type">{useNavigationType()}</span>
    </>
  );
}

const currentPath = (): string | null => screen.getByTestId('location').textContent;
const navigationType = (): string | null => screen.getByTestId('navigation-type').textContent;

const calls = {
  pinned: [] as boolean[],
  closes: 0,
};

function renderPanel({ docked = false, pinned = false }: { docked?: boolean; pinned?: boolean } = {}) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={['/agents/a1/sessions/s1']}>
        <Probe />
        <SessionsPanel
          agentId="a1"
          sessionId="s1"
          docked={docked}
          pinned={pinned}
          onPinnedChange={(next) => {
            calls.pinned.push(next);
          }}
          onClose={() => {
            calls.closes += 1;
          }}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  api.agents = [agent('a1', 'desktop', true)];
  api.sessions = [];
  calls.pinned.length = 0;
  calls.closes = 0;
});

afterEach(() => {
  cleanup();
  resetSessionsSynced();
  useLastSessionStore.setState({ lastUsedSessionIds: {} });
  useSelectedAgentStore.setState({ selectedAgentId: null });
});

describe('SessionsPanel rows', () => {
  it('reads as reconnecting until the list is agent-authoritative', () => {
    // A cached list from a dropped tunnel still has rows in it; showing them as
    // switchable would attach to sessions the agent may no longer have.
    api.sessions = [session('s1')];
    renderPanel();

    expect(screen.getByText('Reconnecting…')).toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Sessions' })).toBeNull();
  });

  it('lists the synced sessions with the current one marked, plus kind, project and attention', async () => {
    markSessionsSynced('a1');
    api.sessions = [
      session('s1', { title: 'fix: login', project_name: 'app', attention: 'awaiting_permission' }),
      session('s2', { kind: 'custom_ab12cd34ef56', project_name: 'api' }),
    ];
    renderPanel();

    const list = await screen.findByRole('list', { name: 'Sessions' });
    const current = within(list).getByRole('button', { name: /fix: login/ });
    expect(current).toHaveAttribute('aria-current', 'true');
    expect(within(current).getByText('Claude Code · app')).toBeInTheDocument();
    expect(within(current).getByText('Needs permission')).toBeInTheDocument();

    // No title yet: the composite headline, the custom kind resolved through
    // the live registry, and no chip for a session that needs nothing.
    const other = await within(list).findByRole('button', { name: /Aider · api/ });
    expect(other).not.toHaveAttribute('aria-current');
    expect(within(other).queryByText(/Needs permission|Ready|Ended/)).toBeNull();
  });

  it('says so when the agent has no sessions, and still offers to create one', async () => {
    markSessionsSynced('a1');
    renderPanel();

    expect(await screen.findByText('No active sessions')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '+ New session' })).toHaveAttribute(
      'href',
      '/agents/a1/new-session',
    );
  });

  it('switches by replacing the route and remembers the pick for auto-resume', async () => {
    // Replace, never push: Back from the new terminal must still return to
    // wherever this one was opened from, as on both mobile clients.
    markSessionsSynced('a1');
    api.sessions = [session('s1', { title: 'one' }), session('s2', { title: 'two' })];
    renderPanel();

    fireEvent.click(await screen.findByRole('button', { name: /two/ }));

    expect(currentPath()).toBe('/agents/a1/sessions/s2');
    expect(navigationType()).toBe('REPLACE');
    expect(useLastSessionStore.getState().lastUsedSessionIds).toEqual({ a1: 's2' });
    expect(calls.closes).toBe(0);
  });

  it('closes rather than re-opening the session already on screen', async () => {
    markSessionsSynced('a1');
    api.sessions = [session('s1', { title: 'one' })];
    renderPanel();

    fireEvent.click(await screen.findByRole('button', { name: /one/ }));

    expect(currentPath()).toBe('/agents/a1/sessions/s1');
    expect(calls.closes).toBe(1);
    expect(useLastSessionStore.getState().lastUsedSessionIds).toEqual({});
  });
});

describe('SessionsPanel agents', () => {
  it('names the connected agent and lists only the others, offline ones disabled', async () => {
    api.agents = [agent('a1', 'desktop', true), agent('a2', 'laptop', false), agent('a3', 'server', true)];
    renderPanel();

    const others = await screen.findByRole('list', { name: 'Other agents' });
    expect(screen.getByText('desktop')).toBeInTheDocument();
    expect(within(others).queryByRole('button', { name: /desktop/ })).toBeNull();
    expect(within(others).getByRole('button', { name: /laptop/ })).toBeDisabled();
    expect(within(others).getByRole('button', { name: /laptop/ })).toHaveTextContent('Offline');
    expect(within(others).getByRole('button', { name: /server/ })).toBeEnabled();
  });

  it('selects an online agent and goes to its sessions list', async () => {
    api.agents = [agent('a1', 'desktop', true), agent('a3', 'server', true)];
    renderPanel();
    const others = await screen.findByRole('list', { name: 'Other agents' });

    fireEvent.click(within(others).getByRole('button', { name: /server/ }));

    expect(useSelectedAgentStore.getState().selectedAgentId).toBe('a3');
    expect(currentPath()).toBe('/');
  });

  it('shows no agents section when the account owns only this agent', async () => {
    renderPanel();

    await screen.findByText('desktop');
    expect(screen.queryByRole('list', { name: 'Other agents' })).toBeNull();
    expect(screen.queryByText('Agents')).toBeNull();
  });

  it('reports the connected agent offline when the list says so', async () => {
    api.agents = [agent('a1', 'desktop', false)];
    renderPanel();

    await screen.findByText('desktop');
    expect(screen.getByText('Offline')).toBeInTheDocument();
  });
});

describe('SessionsPanel chrome', () => {
  it('takes focus on the heading when it opens as an overlay, not when docked', () => {
    renderPanel({ docked: false });
    expect(screen.getByRole('heading', { name: 'Sessions' })).toHaveFocus();
    cleanup();

    // The docked rail is part of the page; stealing focus from the grid on
    // every terminal visit would be the opposite of a convenience.
    renderPanel({ docked: true });
    expect(screen.getByRole('heading', { name: 'Sessions' })).not.toHaveFocus();
  });

  it('offers a close button only in the overlay form', () => {
    renderPanel({ docked: false });
    fireEvent.click(screen.getByRole('button', { name: 'Close sessions panel' }));
    expect(calls.closes).toBe(1);
    cleanup();

    renderPanel({ docked: true });
    expect(screen.queryByRole('button', { name: 'Close sessions panel' })).toBeNull();
  });

  it('toggles the pin and reflects it as pressed', () => {
    renderPanel({ pinned: false });
    const pin = screen.getByRole('button', { name: 'Pin' });
    expect(pin).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(pin);
    expect(calls.pinned).toEqual([true]);
    cleanup();

    renderPanel({ pinned: true });
    const unpin = screen.getByRole('button', { name: 'Unpin' });
    expect(unpin).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(unpin);
    expect(calls.pinned).toEqual([true, false]);
  });
});

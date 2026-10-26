import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation, useNavigationType } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubLocalStorage } from '../../test/localStorageStub';
import type { Agent } from '../../api/agentRepository';
import type { SessionResponse } from '../../protocol/dto/session';

const api = vi.hoisted(() => ({
  agents: [] as Agent[],
  sessions: [] as SessionResponse[],
  stops: [] as { sessionId: string; force: boolean }[],
  stopResult: (): Promise<void> => Promise.resolve(),
  /** Navigations and evictions in call order — the DOM probe lags a render behind. */
  trace: [] as string[],
}));

vi.mock('react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router')>();
  return {
    ...actual,
    useNavigate: () => {
      const navigate = actual.useNavigate();
      return ((to: unknown, options?: unknown) => {
        if (typeof to === 'string') api.trace.push(`navigate:${to}`);
        return (navigate as (to: unknown, options?: unknown) => unknown)(to, options);
      }) as ReturnType<typeof actual.useNavigate>;
    },
  };
});

vi.mock('../../api/agentRepository', () => ({
  listAgents: () => Promise.resolve(api.agents),
}));

vi.mock('../../api/sessionRepository', () => ({
  listSessions: () => Promise.resolve(api.sessions),
  stopSession: (_agentId: string, sessionId: string, force: boolean) => {
    api.stops.push({ sessionId, force });
    return api.stopResult();
  },
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
const { ApiError } = await import('../../api/errors');
const { useToastStore } = await import('../../stores/toastStore');
const { useStopHandOffStore } = await import('../../stores/stopHandOffStore');
const { queryKeys } = await import('../../lib/queryKeys');
const { setTerminalEvictor } = await import('../../terminal/terminalEviction');
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

function renderPanel({
  docked = false,
  pinned = false,
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
  handOff = null,
}: {
  docked?: boolean;
  pinned?: boolean;
  client?: QueryClient;
  handOff?: { departing: { session: SessionResponse; index: number } | null } | null;
} = {}) {
  return render(
    <QueryClientProvider client={client}>
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
          handOff={handOff}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  api.agents = [agent('a1', 'desktop', true)];
  api.sessions = [];
  api.stops = [];
  api.trace = [];
  api.stopResult = () => Promise.resolve();
  calls.pinned.length = 0;
  calls.closes = 0;
});

afterEach(() => {
  cleanup();
  resetSessionsSynced();
  useLastSessionStore.setState({ lastUsedSessionIds: {} });
  useSelectedAgentStore.setState({ selectedAgentId: null });
  useToastStore.setState({ toasts: [] });
  useStopHandOffStore.setState({ handOff: null });
  setTerminalEvictor(null);
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

describe('SessionsPanel stop', () => {
  const toastMessages = () => useToastStore.getState().toasts.map((t) => [t.message, t.kind]);
  const rowItem = (list: HTMLElement, title: string): HTMLElement => {
    const item = within(list).getByRole('button', { name: new RegExp(title) }).closest('li');
    if (item === null) throw new Error(`no list item for ${title}`);
    return item;
  };

  it('offers Stop only on running and starting rows', async () => {
    markSessionsSynced('a1');
    api.sessions = [
      session('s1', { title: 'one' }),
      session('s2', { title: 'two', state: 'starting' }),
      session('s3', { title: 'three', state: 'stopped' }),
    ];
    renderPanel();

    const list = await screen.findByRole('list', { name: 'Sessions' });
    expect(within(rowItem(list, 'one')).getByRole('button', { name: 'Stop session' })).toBeEnabled();
    expect(within(rowItem(list, 'two')).getByRole('button', { name: 'Stop session' })).toBeEnabled();
    expect(within(rowItem(list, 'three')).queryByRole('button', { name: 'Stop session' })).toBeNull();
  });

  it('stops another session with the chosen force flag and stays on this terminal', async () => {
    markSessionsSynced('a1');
    api.sessions = [session('s1', { title: 'one' }), session('s2', { title: 'two' })];
    useLastSessionStore.setState({ lastUsedSessionIds: { a1: 's1' } });
    renderPanel();
    const list = await screen.findByRole('list', { name: 'Sessions' });

    fireEvent.click(within(rowItem(list, 'two')).getByRole('button', { name: 'Stop session' }));
    fireEvent.click(screen.getByRole('button', { name: 'Force Stop' }));

    await waitFor(() => {
      expect(toastMessages()).toEqual([['Session stopped.', 'success']]);
    });
    expect(api.stops).toEqual([{ sessionId: 's2', force: true }]);
    expect(currentPath()).toBe('/agents/a1/sessions/s1');
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(calls.closes).toBe(0);
    expect(useLastSessionStore.getState().lastUsedSessionIds).toEqual({ a1: 's1' });
  });

  it('leaves for the sessions list before evicting the terminal it stopped', async () => {
    markSessionsSynced('a1');
    api.sessions = [session('s1', { title: 'one' })];
    useLastSessionStore.setState({ lastUsedSessionIds: { a1: 's1', a2: 's9' } });
    setTerminalEvictor((_agentId, ids) => {
      api.trace.push(`evict:${ids.join(',')}`);
    });
    renderPanel();
    const list = await screen.findByRole('list', { name: 'Sessions' });

    fireEvent.click(within(rowItem(list, 'one')).getByRole('button', { name: 'Stop session' }));
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));

    await waitFor(() => {
      expect(currentPath()).toBe('/');
    });
    expect(navigationType()).toBe('REPLACE');
    expect(api.stops).toEqual([{ sessionId: 's1', force: false }]);
    // Evicting first would tear the live view down under the user.
    expect(api.trace).toEqual(['navigate:/', 'evict:s1']);
    expect(toastMessages()).toEqual([['Session stopped.', 'success']]);
    // Otherwise an unclaimed auto-resume sends the list straight back into it.
    expect(useLastSessionStore.getState().lastUsedSessionIds).toEqual({ a2: 's9' });
  });

  it('moves to the nearest open session below instead of the list, before evicting', async () => {
    markSessionsSynced('a1');
    api.sessions = [
      session('s0', { title: 'zero' }),
      session('s1', { title: 'one' }),
      session('s2', { title: 'two' }),
    ];
    useLastSessionStore.setState({ lastUsedSessionIds: { a1: 's1', a2: 's9' } });
    setTerminalEvictor((_agentId, ids) => {
      api.trace.push(`evict:${ids.join(',')}`);
    });
    renderPanel();
    const list = await screen.findByRole('list', { name: 'Sessions' });

    fireEvent.click(within(rowItem(list, 'one')).getByRole('button', { name: 'Stop session' }));
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));

    await waitFor(() => {
      expect(currentPath()).toBe('/agents/a1/sessions/s2');
    });
    expect(navigationType()).toBe('REPLACE');
    expect(api.trace).toEqual(['navigate:/agents/a1/sessions/s2', 'evict:s1']);
    expect(useLastSessionStore.getState().lastUsedSessionIds).toEqual({ a1: 's2', a2: 's9' });
    expect(toastMessages()).toEqual([['Session stopped.', 'success']]);
  });

  it('hands the next terminal the stopped row and the open overlay', async () => {
    markSessionsSynced('a1');
    api.sessions = [
      session('s0', { title: 'zero' }),
      session('s1', { title: 'one' }),
      session('s2', { title: 'two' }),
    ];
    renderPanel();
    const list = await screen.findByRole('list', { name: 'Sessions' });

    fireEvent.click(within(rowItem(list, 'one')).getByRole('button', { name: 'Stop session' }));
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));

    await waitFor(() => {
      expect(currentPath()).toBe('/agents/a1/sessions/s2');
    });
    expect(useStopHandOffStore.getState().handOff).toEqual({
      agentId: 'a1',
      sessionId: 's2',
      departing: { session: session('s1', { title: 'one' }), index: 1 },
      overlayOpen: true,
    });
  });

  it('does not ask a docked rail to reopen as an overlay', async () => {
    markSessionsSynced('a1');
    api.sessions = [session('s1', { title: 'one' }), session('s2', { title: 'two' })];
    renderPanel({ docked: true, pinned: true });
    const list = await screen.findByRole('list', { name: 'Sessions' });

    fireEvent.click(within(rowItem(list, 'one')).getByRole('button', { name: 'Stop session' }));
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));

    await waitFor(() => {
      expect(currentPath()).toBe('/agents/a1/sessions/s2');
    });
    expect(useStopHandOffStore.getState().handOff?.overlayOpen).toBe(false);
  });

  it('keeps another stopped row on screen as a leaving, inert item after the push drops it', async () => {
    markSessionsSynced('a1');
    api.sessions = [
      session('s1', { title: 'one' }),
      session('s2', { title: 'two' }),
      session('s3', { title: 'three' }),
    ];
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderPanel({ client });
    const list = await screen.findByRole('list', { name: 'Sessions' });

    fireEvent.click(within(rowItem(list, 'two')).getByRole('button', { name: 'Stop session' }));
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    await waitFor(() => {
      expect(toastMessages()).toEqual([['Session stopped.', 'success']]);
    });
    client.setQueryData(queryKeys.sessions('a1'), [
      session('s1', { title: 'one' }),
      session('s3', { title: 'three' }),
    ]);

    await waitFor(() => {
      expect(list.querySelectorAll('li')).toHaveLength(3);
    });
    const items = [...list.querySelectorAll('li')];
    expect(items[1]).toHaveClass('sessions-panel-item-leaving');
    expect(items[1]).toHaveAttribute('aria-hidden', 'true');
    expect(items[1]).toHaveAttribute('inert');
    // Hidden from the accessibility tree: only the live rows are buttons.
    expect(within(list).queryByRole('button', { name: /two/ })).toBeNull();
    expect(items[0]).not.toHaveClass('sessions-panel-item-leaving');
  });

  it('opens on a hand-off with the stopped row leaving at its old place and the new row settling', async () => {
    markSessionsSynced('a1');
    api.sessions = [session('s0', { title: 'zero' }), session('s1', { title: 'one' })];
    renderPanel({
      handOff: { departing: { session: session('sx', { title: 'gone' }), index: 1 } },
    });
    const list = await screen.findByRole('list', { name: 'Sessions' });

    const items = [...list.querySelectorAll('li')];
    expect(items).toHaveLength(3);
    expect(items[1]).toHaveClass('sessions-panel-item-leaving');
    expect(rowItem(list, 'one')).toHaveClass(
      'sessions-panel-item-current',
      'sessions-panel-item-arriving',
    );
    expect(rowItem(list, 'zero')).not.toHaveClass('sessions-panel-item-arriving');
  });

  it('picks by the list as confirmed even when the stopped row is pushed away mid-stop', async () => {
    markSessionsSynced('a1');
    api.sessions = [
      session('s0', { title: 'zero' }),
      session('s1', { title: 'one' }),
      session('s2', { title: 'two' }),
      session('s3', { title: 'three' }),
    ];
    let resolveStop = () => {};
    api.stopResult = () =>
      new Promise<void>((resolve) => {
        resolveStop = resolve;
      });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderPanel({ client });
    const list = await screen.findByRole('list', { name: 'Sessions' });

    fireEvent.click(within(rowItem(list, 'one')).getByRole('button', { name: 'Stop session' }));
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    await waitFor(() => {
      expect(api.stops).toEqual([{ sessionId: 's1', force: false }]);
    });
    // The agent's push lands first: the stopped row is gone and its old
    // neighbour below has ended, so only the snapshot still knows the order.
    client.setQueryData(queryKeys.sessions('a1'), [
      session('s0', { title: 'zero' }),
      session('s2', { title: 'two', state: 'stopped' }),
      session('s3', { title: 'three' }),
    ]);
    await waitFor(() => {
      expect(within(list).queryByRole('button', { name: /one/ })).toBeNull();
    });
    resolveStop();

    await waitFor(() => {
      expect(currentPath()).toBe('/agents/a1/sessions/s0');
    });
    expect(useLastSessionStore.getState().lastUsedSessionIds).toEqual({ a1: 's0' });
  });

  it('treats a 404 on the current session as already stopped and still leaves', async () => {
    markSessionsSynced('a1');
    api.sessions = [session('s1', { title: 'one' })];
    api.stopResult = () =>
      Promise.reject(
        new ApiError({ status: 404, message: 'Not found', code: null, retryAfterSeconds: null, detail: null }),
      );
    renderPanel();
    const list = await screen.findByRole('list', { name: 'Sessions' });

    fireEvent.click(within(rowItem(list, 'one')).getByRole('button', { name: 'Stop session' }));
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));

    await waitFor(() => {
      expect(currentPath()).toBe('/');
    });
    expect(toastMessages()).toEqual([['Session stopped.', 'success']]);
  });

  it('reports a failed stop and stays put', async () => {
    markSessionsSynced('a1');
    api.sessions = [session('s1', { title: 'one' })];
    api.stopResult = () =>
      Promise.reject(
        new ApiError({ status: 500, message: 'boom', code: null, retryAfterSeconds: null, detail: null }),
      );
    renderPanel();
    const list = await screen.findByRole('list', { name: 'Sessions' });

    fireEvent.click(within(rowItem(list, 'one')).getByRole('button', { name: 'Stop session' }));
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));

    await waitFor(() => {
      expect(toastMessages()).toEqual([['Failed to stop session: boom', 'error']]);
    });
    expect(currentPath()).toBe('/agents/a1/sessions/s1');
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });
});

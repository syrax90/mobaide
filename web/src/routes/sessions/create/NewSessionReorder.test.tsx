/**
 * The wizard's reorder wiring: the project rows save the agent's own project
 * order, the mode cards save the account's mode order, both show a move at
 * once and both revert with a toast when the save fails.
 *
 * Moves are driven through the handles' arrow keys — the same `onMove` a drop
 * fires, with no layout to fake. The drag gesture itself is covered by
 * `useDragReorder.test.tsx`.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubLocalStorage } from '../../../test/localStorageStub';
import type { ProjectInfo } from '../../../api/projectRepository';
import type { SessionModeOrder } from '../../../protocol/dto/userEvents';

// No agent token means no URL, so the wizard's agent channel never constructs a
// socket — the seam `NewSession.test.tsx` uses.
vi.mock('../../../auth/agentTokens', () => ({
  ensureAgentToken: () => Promise.resolve(null),
  clearAgentToken: () => undefined,
}));

const api = vi.hoisted(
  (): {
    projects: string[];
    /** The lists of agents other than `agent-1`, which lists `projects`. */
    otherAgentProjects: Record<string, string[]>;
    reorders: string[][];
    /** The agent each `reorders` entry was sent for. */
    reorderAgents: string[];
    modeOrder: SessionModeOrder;
    modeSaves: string[][];
    /** While set, the matching save rejects as the server would on a 5xx. */
    failProjectSave: boolean;
    failModeSave: boolean;
    /** While set, a save answers only when the test calls its queued release. */
    holdSaves: boolean;
    releaseSave: (() => void)[];
    /** While set, a GET answers only when the test calls `settleGet`. */
    holdGet: boolean;
    settleGet: (() => void) | null;
  } => ({
    projects: [],
    otherAgentProjects: {},
    reorders: [],
    reorderAgents: [],
    modeOrder: { order: [], version: 0, updated_at: undefined },
    modeSaves: [],
    failProjectSave: false,
    failModeSave: false,
    holdSaves: false,
    releaseSave: [],
    holdGet: false,
    settleGet: null,
  }),
);

vi.mock('../../../api/projectRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/projectRepository')>();
  const { projectInfoSchema } = await import('../../../protocol/dto/projects');
  const rows = (names: readonly string[]): ProjectInfo[] =>
    names.map((name, order) => projectInfoSchema.parse({ name, path: `/srv/${name}`, order }));
  return {
    ...actual,
    listProjects: (agentId: string) =>
      Promise.resolve(rows(api.otherAgentProjects[agentId] ?? api.projects)),
    // The 200 body is the agent's re-sorted list, which the wizard writes
    // straight into the cache — so the fake answers with it.
    reorderProjects: (agentId: string, names: readonly string[]) => {
      api.reorders.push([...names]);
      api.reorderAgents.push(agentId);
      if (api.failProjectSave) return Promise.reject(new Error('agent unreachable'));
      if (api.otherAgentProjects[agentId] === undefined) api.projects = [...names];
      else api.otherAgentProjects[agentId] = [...names];
      const answer = rows(names);
      if (!api.holdSaves) return Promise.resolve(answer);
      return new Promise<ProjectInfo[]>((resolve) => {
        api.releaseSave.push(() => {
          resolve(answer);
        });
      });
    },
  };
});

vi.mock('../../../api/sessionModeOrderRepository', () => ({
  getSessionModeOrder: () => {
    // The server's answer is fixed when the request reaches it, not when the
    // response lands.
    const answer = api.modeOrder;
    if (!api.holdGet) return Promise.resolve(answer);
    return new Promise<SessionModeOrder>((resolve) => {
      api.settleGet = () => {
        resolve(answer);
      };
    });
  },
  saveSessionModeOrder: (order: readonly string[]) => {
    api.modeSaves.push([...order]);
    if (api.failModeSave) return Promise.reject(new Error('relay unreachable'));
    api.modeOrder = {
      order: [...order],
      version: api.modeOrder.version + 1,
      updated_at: '2026-11-04T10:00:00Z',
    };
    const answer = api.modeOrder;
    if (!api.holdSaves) return Promise.resolve(answer);
    return new Promise<SessionModeOrder>((resolve) => {
      api.releaseSave.push(() => {
        resolve(answer);
      });
    });
  },
}));

// Every CLI reported present, so no card is disabled.
vi.mock('../../../api/agentCapabilities', async () => {
  const { agentVersionSchema } = await import('../../../protocol/dto/agentVersion');
  const info = agentVersionSchema.parse({
    name: 'agent-1',
    server_version: '1.0.0',
    protocol_versions: [8],
    min_client_version: 1,
    capabilities: {
      cli: {
        claude: { available: true },
        codex: { available: true },
        antigravity: { available: true },
      },
    },
  });
  return { fetchAgentCapabilities: () => Promise.resolve(info) };
});

vi.mock('../../../api/customAgentTypeRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/customAgentTypeRepository')>();
  return { ...actual, listCustomAgentTypes: () => Promise.resolve([]) };
});

// Before any persisted store is imported.
stubLocalStorage();

const { queryKeys } = await import('../../../lib/queryKeys');
const { useToastStore } = await import('../../../stores/toastStore');
const { useLaunchPrefsStore } = await import('../../../stores/launchPrefsStore');
const { stopAllRealtime } = await import('../../../realtime');
const { useSessionStore } = await import('../../../auth/sessionStore');
const { NewSession } = await import('./NewSession');

const DEFAULT_MODES = ['Claude Code', 'Codex', 'Antigravity', 'Auto Scan', 'Terminal'];

let client: QueryClient;

/** Row order, read from the drag handles' accessible names. */
const handleOrder = (): string[] =>
  screen
    .queryAllByRole('button', { name: /^Reorder / })
    .map((button) => (button.getAttribute('aria-label') ?? '').slice('Reorder '.length));

const handle = (label: string): HTMLElement =>
  screen.getByRole('button', { name: `Reorder ${label}` });

const toasts = (): string[] =>
  useToastStore.getState().toasts.map((toast) => toast.message);

const cachedProjectNames = (agentId = 'agent-1'): string[] | undefined =>
  client.getQueryData<ProjectInfo[]>(queryKeys.projects(agentId))?.map((p) => p.name);

/** Saves waiting in their list's queue for the one ahead of them. */
const queuedSaves = (): number =>
  client.getMutationCache().findAll({ predicate: (mutation) => mutation.state.isPaused }).length;

/** Answer the oldest held save and let its `onSuccess` run. */
async function releaseOldestSave() {
  await act(async () => {
    api.releaseSave.shift()?.();
    await Promise.resolve();
  });
}

function signInAs(userId: string) {
  useSessionStore.setState({
    session: {
      accessToken: `token-${userId}`,
      userId,
      email: `${userId}@example.com`,
      role: 'user',
      scheduledDeletionAt: null,
    },
  });
}

/** Moves the route to `agent-2` under the one wizard the router keeps mounted. */
function SwitchAgent() {
  const navigate = useNavigate();
  return (
    <button
      type="button"
      data-testid="switch-to-agent-2"
      onClick={() => {
        void navigate('/agents/agent-2/new-session');
      }}
    />
  );
}

async function renderWizard(search: string) {
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/agents/agent-1/new-session${search}`]}>
        <SwitchAgent />
        <Routes>
          <Route path="/agents/:agentId/new-session" element={<NewSession />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await waitFor(() => {
    expect(screen.queryByText('Loading projects…')).not.toBeInTheDocument();
  });
}

async function renderModeStep() {
  await renderWizard('?step=mode&project=alpha');
  await screen.findByRole('heading', { name: 'What should it run?' });
}

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  api.projects = ['alpha', 'beta', 'gamma'];
  api.otherAgentProjects = {};
  api.reorders.length = 0;
  api.reorderAgents.length = 0;
  api.modeOrder = { order: [], version: 0, updated_at: undefined };
  api.modeSaves.length = 0;
  api.failProjectSave = false;
  api.failModeSave = false;
  api.holdSaves = false;
  api.releaseSave.length = 0;
  api.holdGet = false;
  api.settleGet = null;
  useToastStore.setState({ toasts: [] });
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  stopAllRealtime();
  client.clear();
  useLaunchPrefsStore.setState({
    claude: null,
    autoScan: null,
    codex: null,
    antigravity: null,
    lastProjectName: null,
  });
  useSessionStore.setState({ session: null });
  window.localStorage.clear();
});

describe('NewSession project reorder', () => {
  it('saves the whole name list as the agent’s order and shows the move at once', async () => {
    await renderWizard('');
    expect(handleOrder()).toEqual(['alpha', 'beta', 'gamma']);

    fireEvent.keyDown(handle('alpha'), { key: 'ArrowDown' });

    expect(handleOrder()).toEqual(['beta', 'alpha', 'gamma']);
    expect(screen.getByRole('status')).toHaveTextContent('alpha moved to position 2 of 3');
    await waitFor(() => {
      expect(api.reorders).toEqual([['beta', 'alpha', 'gamma']]);
    });
    await waitFor(() => {
      expect(
        client.getQueryData<ProjectInfo[]>(queryKeys.projects('agent-1'))?.map((p) => p.name),
      ).toEqual(['beta', 'alpha', 'gamma']);
    });
    expect(handleOrder()).toEqual(['beta', 'alpha', 'gamma']);
    expect(toasts()).toEqual([]);
  });

  it('reverts to the agent’s order and says so when the save fails', async () => {
    api.failProjectSave = true;
    await renderWizard('');

    fireEvent.keyDown(handle('gamma'), { key: 'ArrowUp' });
    expect(handleOrder()).toEqual(['alpha', 'gamma', 'beta']);

    await waitFor(() => {
      expect(toasts()).toEqual(['Reorder failed — reverted.']);
    });
    expect(handleOrder()).toEqual(['alpha', 'beta', 'gamma']);
  });

  it('sends only the save in flight and the last of a burst of moves', async () => {
    api.projects = ['alpha', 'beta', 'gamma', 'delta'];
    api.holdSaves = true;
    await renderWizard('');

    fireEvent.keyDown(handle('alpha'), { key: 'ArrowDown' });
    await waitFor(() => {
      expect(api.reorders).toHaveLength(1);
    });
    fireEvent.keyDown(handle('alpha'), { key: 'ArrowDown' });
    fireEvent.keyDown(handle('alpha'), { key: 'ArrowDown' });
    expect(handleOrder()).toEqual(['beta', 'gamma', 'delta', 'alpha']);

    await releaseOldestSave();
    await waitFor(() => {
      expect(api.reorders).toHaveLength(2);
    });
    expect(api.reorders).toEqual([
      ['beta', 'alpha', 'gamma', 'delta'],
      ['beta', 'gamma', 'delta', 'alpha'],
    ]);
    // The first answer is in the cache, but the newer move stays on screen.
    expect(cachedProjectNames()).toEqual(['beta', 'alpha', 'gamma', 'delta']);
    expect(handleOrder()).toEqual(['beta', 'gamma', 'delta', 'alpha']);

    await releaseOldestSave();
    await waitFor(() => {
      expect(cachedProjectNames()).toEqual(['beta', 'gamma', 'delta', 'alpha']);
    });
    expect(handleOrder()).toEqual(['beta', 'gamma', 'delta', 'alpha']);
    expect(api.reorders).toHaveLength(2);
    expect(toasts()).toEqual([]);
  });

  it('keeps a save on the agent it was made on after the route moves to another agent', async () => {
    // A pending save's options follow the current render, which after the
    // move belongs to agent-2.
    api.otherAgentProjects = { 'agent-2': ['delta', 'epsilon'] };
    api.holdSaves = true;
    await renderWizard('');

    fireEvent.keyDown(handle('alpha'), { key: 'ArrowDown' });
    await waitFor(() => {
      expect(api.reorders).toHaveLength(1);
    });
    fireEvent.keyDown(handle('alpha'), { key: 'ArrowDown' });
    await waitFor(() => {
      expect(queuedSaves()).toBe(1);
    });

    fireEvent.click(screen.getByTestId('switch-to-agent-2'));
    await waitFor(() => {
      expect(handleOrder()).toEqual(['delta', 'epsilon']);
    });

    await releaseOldestSave();
    await waitFor(() => {
      expect(api.reorders).toHaveLength(2);
    });
    await releaseOldestSave();
    await waitFor(() => {
      expect(client.isMutating()).toBe(0);
    });

    expect(api.reorders).toEqual([
      ['beta', 'alpha', 'gamma'],
      ['beta', 'gamma', 'alpha'],
    ]);
    expect(api.reorderAgents).toEqual(['agent-1', 'agent-1']);
    expect(cachedProjectNames()).toEqual(['beta', 'gamma', 'alpha']);
    expect(cachedProjectNames('agent-2')).toEqual(['delta', 'epsilon']);
    expect(handleOrder()).toEqual(['delta', 'epsilon']);
    expect(toasts()).toEqual([]);
  });

  it('still sends a save queued on one agent after a drop on another', async () => {
    // agent-1's second save waits behind its first; a drop on agent-2 must
    // neither skip it nor strand it in the queue.
    api.otherAgentProjects = { 'agent-2': ['delta', 'epsilon'] };
    api.holdSaves = true;
    await renderWizard('');

    fireEvent.keyDown(handle('alpha'), { key: 'ArrowDown' });
    await waitFor(() => {
      expect(api.reorders).toHaveLength(1);
    });
    fireEvent.keyDown(handle('alpha'), { key: 'ArrowDown' });
    await waitFor(() => {
      expect(queuedSaves()).toBe(1);
    });

    fireEvent.click(screen.getByTestId('switch-to-agent-2'));
    await waitFor(() => {
      expect(handleOrder()).toEqual(['delta', 'epsilon']);
    });
    fireEvent.keyDown(handle('delta'), { key: 'ArrowDown' });
    expect(handleOrder()).toEqual(['epsilon', 'delta']);

    // One queue serves every agent: the agent-2 save waits behind both
    // agent-1 saves, so only the first has reached the server so far.
    await waitFor(() => {
      expect(queuedSaves()).toBe(2);
    });
    expect(api.reorderAgents).toEqual(['agent-1']);

    await releaseOldestSave();
    await waitFor(() => {
      expect(api.reorders).toHaveLength(2);
    });
    await releaseOldestSave();
    await waitFor(() => {
      expect(api.reorders).toHaveLength(3);
    });
    await releaseOldestSave();
    await waitFor(() => {
      expect(client.isMutating()).toBe(0);
    });

    expect(api.reorders).toEqual([
      ['beta', 'alpha', 'gamma'],
      ['beta', 'gamma', 'alpha'],
      ['epsilon', 'delta'],
    ]);
    expect(api.reorderAgents).toEqual(['agent-1', 'agent-1', 'agent-2']);
    expect(cachedProjectNames()).toEqual(['beta', 'gamma', 'alpha']);
    expect(cachedProjectNames('agent-2')).toEqual(['epsilon', 'delta']);
    expect(handleOrder()).toEqual(['epsilon', 'delta']);
  });

});

describe('NewSession mode reorder', () => {
  it('shows the cards in the account’s saved order', async () => {
    api.modeOrder = { order: ['shell', 'codex'], version: 2, updated_at: 'u' };

    await renderModeStep();

    await waitFor(() => {
      expect(handleOrder()).toEqual(['Terminal', 'Codex', 'Claude Code', 'Antigravity', 'Auto Scan']);
    });
  });

  it('saves the whole mode id list and shows the move at once', async () => {
    await renderModeStep();
    await waitFor(() => {
      expect(handleOrder()).toEqual(DEFAULT_MODES);
    });

    fireEvent.keyDown(handle('Terminal'), { key: 'ArrowUp' });

    expect(handleOrder()).toEqual(['Claude Code', 'Codex', 'Antigravity', 'Terminal', 'Auto Scan']);
    expect(screen.getByRole('status')).toHaveTextContent('Terminal moved to position 4 of 5');
    await waitFor(() => {
      expect(api.modeSaves).toEqual([['claude', 'codex', 'antigravity', 'shell', 'auto_scan']]);
    });
    await waitFor(() => {
      expect(client.getQueryData<SessionModeOrder>(queryKeys.sessionModeOrder())).toMatchObject({
        order: ['claude', 'codex', 'antigravity', 'shell', 'auto_scan'],
        version: 1,
      });
    });
    expect(handleOrder()).toEqual(['Claude Code', 'Codex', 'Antigravity', 'Terminal', 'Auto Scan']);
  });

  it('reverts to the saved order and says so when the save fails', async () => {
    api.failModeSave = true;
    await renderModeStep();
    await waitFor(() => {
      expect(handleOrder()).toEqual(DEFAULT_MODES);
    });

    fireEvent.keyDown(handle('Claude Code'), { key: 'ArrowDown' });
    expect(handleOrder()[0]).toBe('Codex');

    await waitFor(() => {
      expect(toasts()).toEqual(['Reorder failed — reverted.']);
    });
    expect(handleOrder()).toEqual(DEFAULT_MODES);
  });

  it('sends only the save in flight and the last of a burst of moves', async () => {
    api.holdSaves = true;
    await renderModeStep();
    await waitFor(() => {
      expect(handleOrder()).toEqual(DEFAULT_MODES);
    });

    fireEvent.keyDown(handle('Terminal'), { key: 'ArrowUp' });
    await waitFor(() => {
      expect(api.modeSaves).toHaveLength(1);
    });
    fireEvent.keyDown(handle('Terminal'), { key: 'ArrowUp' });
    fireEvent.keyDown(handle('Terminal'), { key: 'ArrowUp' });
    expect(handleOrder()).toEqual(['Claude Code', 'Terminal', 'Codex', 'Antigravity', 'Auto Scan']);

    await releaseOldestSave();
    await waitFor(() => {
      expect(api.modeSaves).toHaveLength(2);
    });
    expect(api.modeSaves).toEqual([
      ['claude', 'codex', 'antigravity', 'shell', 'auto_scan'],
      ['claude', 'shell', 'codex', 'antigravity', 'auto_scan'],
    ]);
    expect(handleOrder()).toEqual(['Claude Code', 'Terminal', 'Codex', 'Antigravity', 'Auto Scan']);

    await releaseOldestSave();
    await waitFor(() => {
      expect(client.getQueryData<SessionModeOrder>(queryKeys.sessionModeOrder())).toMatchObject({
        order: ['claude', 'shell', 'codex', 'antigravity', 'auto_scan'],
        version: 2,
      });
    });
    expect(handleOrder()).toEqual(['Claude Code', 'Terminal', 'Codex', 'Antigravity', 'Auto Scan']);
    expect(api.modeSaves).toHaveLength(2);
  });

  it('sends no queued save and keeps no answer once another account signs in', async () => {
    signInAs('u1');
    api.holdSaves = true;
    await renderModeStep();
    await waitFor(() => {
      expect(client.getQueryData(queryKeys.sessionModeOrder())).toBeDefined();
    });

    fireEvent.keyDown(handle('Terminal'), { key: 'ArrowUp' });
    await waitFor(() => {
      expect(api.modeSaves).toHaveLength(1);
    });
    fireEvent.keyDown(handle('Terminal'), { key: 'ArrowUp' });

    act(() => {
      signInAs('u2');
    });
    await releaseOldestSave();

    await waitFor(() => {
      expect(client.isMutating()).toBe(0);
    });
    expect(api.modeSaves).toEqual([['claude', 'codex', 'antigravity', 'shell', 'auto_scan']]);
    expect(client.getQueryData<SessionModeOrder>(queryKeys.sessionModeOrder())).toMatchObject({
      order: [],
      version: 0,
    });
  });

  it('follows an order another device saved', async () => {
    await renderModeStep();
    await waitFor(() => {
      expect(client.getQueryData(queryKeys.sessionModeOrder())).toBeDefined();
    });
    expect(handleOrder()).toEqual(DEFAULT_MODES);

    // What `session_mode_order.updated` does through `sessionModeOrderCache`.
    act(() => {
      client.setQueryData<SessionModeOrder>(queryKeys.sessionModeOrder(), {
        order: ['auto_scan', 'claude'],
        version: 7,
        updated_at: 'u',
      });
    });

    await waitFor(() => {
      expect(handleOrder()).toEqual(['Auto Scan', 'Claude Code', 'Codex', 'Antigravity', 'Terminal']);
    });
  });

  it('keeps a saved order when an older refetch answers after the save', async () => {
    await renderModeStep();
    await waitFor(() => {
      expect(client.getQueryData(queryKeys.sessionModeOrder())).toBeDefined();
    });

    // The user socket reconnects (`refreshOnUserEventsConnect`) and the GET it
    // starts reaches the server before the drop's PUT does.
    api.holdGet = true;
    void client.invalidateQueries({ queryKey: queryKeys.sessionModeOrder() });
    await waitFor(() => {
      expect(api.settleGet).not.toBeNull();
    });

    fireEvent.keyDown(handle('Terminal'), { key: 'ArrowUp' });
    await waitFor(() => {
      expect(client.getQueryData<SessionModeOrder>(queryKeys.sessionModeOrder())).toMatchObject({
        version: 1,
      });
    });

    await act(async () => {
      api.settleGet?.();
      await Promise.resolve();
    });

    await waitFor(() => {
      expect(client.isFetching({ queryKey: queryKeys.sessionModeOrder() })).toBe(0);
    });
    expect(client.getQueryData<SessionModeOrder>(queryKeys.sessionModeOrder())).toMatchObject({
      order: ['claude', 'codex', 'antigravity', 'shell', 'auto_scan'],
      version: 1,
    });
    expect(handleOrder()).toEqual(['Claude Code', 'Codex', 'Antigravity', 'Terminal', 'Auto Scan']);
  });
});

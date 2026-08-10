import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubLocalStorage } from '../../test/localStorageStub';
import type { Agent, AgentCredentials } from '../../api/agentRepository';

const api = vi.hoisted((): {
  agents: Agent[];
  created: string[];
  deleted: string[];
  reregistered: string[];
  credentials: AgentCredentials;
  createError: Error | null;
  deleteError: Error | null;
} => ({
  agents: [],
  created: [],
  deleted: [],
  reregistered: [],
  credentials: { agentId: 'agent-new', name: 'Laptop', token: 'tok-shown-once' },
  createError: null,
  deleteError: null,
}));

const seams = vi.hoisted((): { clearedTokens: string[]; disconnected: string[] } => ({
  clearedTokens: [],
  disconnected: [],
}));

vi.mock('../../api/agentRepository', () => ({
  listAgents: () => Promise.resolve(api.agents),
  createAgent: (name: string) => {
    api.created.push(name);
    const failure = api.createError;
    if (failure !== null) return Promise.reject(failure);
    return Promise.resolve<AgentCredentials>({ ...api.credentials, name });
  },
  reregisterAgent: (agentId: string, name: string) => {
    api.reregistered.push(agentId);
    return Promise.resolve<AgentCredentials>({ agentId, name, token: 'tok-rotated' });
  },
  deleteAgent: (agentId: string) => {
    api.deleted.push(agentId);
    const failure = api.deleteError;
    if (failure !== null) return Promise.reject(failure);
    return Promise.resolve();
  },
}));

// The two side effects an unpair must get exactly right, as recording seams.
vi.mock('../../auth/agentTokens', () => ({
  clearAgentToken: (agentId: string) => {
    seams.clearedTokens.push(agentId);
  },
}));

vi.mock('../../realtime', () => ({
  disconnectAgentEvents: (agentId: string) => {
    seams.disconnected.push(agentId);
  },
}));

// Before any persisted store is imported.
stubLocalStorage();

const { useLastSessionStore } = await import('../../stores/lastSessionStore');
const { useSelectedAgentStore } = await import('../../stores/selectedAgentStore');
// Left real rather than mocked: the bucket it deletes lives in the stubbed
// storage above, so the assertion can be the storage state itself.
const { noteRecentPath, readRecentPaths } = await import('../../stores/recentPaths');
const { Agents } = await import('./Agents');

const agent = (agentId: string, name: string): Agent => ({
  agentId,
  name,
  online: true,
  createdAt: '2026-08-04T10:00:00Z',
  lastSeenAt: null,
  agentVersion: '0.9.9',
  agentProtocolVersion: 7,
});

let client: QueryClient;

function renderAgents() {
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/agents']}>
        <Agents />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  api.agents = [agent('agent-1', 'Workstation'), agent('agent-2', 'Laptop')];
  api.created.length = 0;
  api.deleted.length = 0;
  api.reregistered.length = 0;
  api.createError = null;
  api.deleteError = null;
  seams.clearedTokens.length = 0;
  seams.disconnected.length = 0;
  useSelectedAgentStore.setState({ selectedAgentId: null });
  useLastSessionStore.setState({ lastUsedSessionIds: {} });
});

afterEach(() => {
  cleanup();
  client.clear();
  localStorage.clear();
});

describe('Agents manual add', () => {
  const openAddAndCreate = async (name: string) => {
    renderAgents();
    fireEvent.click(await screen.findByRole('button', { name: 'Add manually' }));
    fireEvent.change(screen.getByLabelText('Agent name'), { target: { value: name } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
  };

  it('shows the one-time credentials after a create', async () => {
    await openAddAndCreate('Laptop');

    expect(await screen.findByText('Agent created')).toBeInTheDocument();
    expect(api.created).toEqual(['Laptop']);
    expect(screen.getByTestId('cred-token')).toHaveTextContent('tok-shown-once');
    expect(screen.getByTestId('cred-agent-id')).toHaveTextContent('agent-new');
  });

  it('replaces the add dialog rather than stacking the panel on it', async () => {
    // One panel at a time: a token shown exactly once must not be reachable
    // from two states at the same time.
    await openAddAndCreate('Laptop');

    expect(await screen.findByText('Agent created')).toBeInTheDocument();
    expect(screen.queryByLabelText('Agent name')).toBeNull();
  });

  it('trims the name before sending it', async () => {
    await openAddAndCreate('   Laptop   ');

    await waitFor(() => {
      expect(api.created).toEqual(['Laptop']);
    });
  });

  it('cannot submit a blank name', async () => {
    renderAgents();
    fireEvent.click(await screen.findByRole('button', { name: 'Add manually' }));
    fireEvent.change(screen.getByLabelText('Agent name'), { target: { value: '   ' } });

    expect(screen.getByRole('button', { name: 'Create' })).toBeDisabled();
    expect(api.created).toEqual([]);
  });

  it('offers Done as the only way out of the credentials panel', async () => {
    // Both mobile clients block every other dismissal route because the token
    // is never retrievable again.
    await openAddAndCreate('Laptop');
    const panel = await screen.findByRole('dialog');

    expect(screen.getByTestId('cred-done')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /cancel|close/i })).toBeNull();

    fireEvent.keyDown(panel, { key: 'Escape' });
    expect(screen.getByTestId('cred-token')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('cred-done'));
    await waitFor(() => {
      expect(screen.queryByTestId('cred-token')).toBeNull();
    });
  });
});

describe('Agents remove', () => {
  /** Open the confirmation for one agent's row; returns the dialog. */
  const openRemoveFor = async (name: string): Promise<HTMLElement> => {
    const row = (await screen.findByText(name)).closest('li') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: 'Remove' }));
    return screen.findByRole('alertdialog');
  };

  const confirmRemove = (dialog: HTMLElement) => {
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }));
  };

  it('clears the token of the removed agent and no other', async () => {
    // `clearAgentToken(id)`, never `sessionStore.agentDisconnect()` — that one
    // drops every agent's tokens and would sign the browser out of the agents
    // it still owns.
    renderAgents();

    confirmRemove(await openRemoveFor('Workstation'));

    await waitFor(() => {
      expect(seams.clearedTokens).toEqual(['agent-1']);
    });
    expect(api.deleted).toEqual(['agent-1']);
    expect(seams.disconnected).toEqual(['agent-1']);
  });

  it('drops the removed agent recent paths and keeps every other agent list', async () => {
    // The bucket is keyed by agent id, so leaving it behind would resurrect a
    // dead machine's directories for whoever next pairs into that id.
    noteRecentPath('agent-1', '/srv/one');
    noteRecentPath('agent-2', '/srv/two');
    renderAgents();

    confirmRemove(await openRemoveFor('Workstation'));

    await waitFor(() => {
      expect(readRecentPaths('agent-1')).toEqual([]);
    });
    expect(readRecentPaths('agent-2')).toEqual(['/srv/two']);
  });

  it('unsticks the sessions list only when it pointed at the removed agent', async () => {
    useSelectedAgentStore.setState({ selectedAgentId: 'agent-2' });
    renderAgents();

    confirmRemove(await openRemoveFor('Workstation'));

    await waitFor(() => {
      expect(api.deleted).toEqual(['agent-1']);
    });
    expect(useSelectedAgentStore.getState().selectedAgentId).toBe('agent-2');
  });

  it('unsticks the sessions list when it pointed at the removed agent', async () => {
    useSelectedAgentStore.setState({ selectedAgentId: 'agent-1' });
    renderAgents();

    confirmRemove(await openRemoveFor('Workstation'));

    await waitFor(() => {
      expect(useSelectedAgentStore.getState().selectedAgentId).toBeNull();
    });
  });

  it('does not touch anything until the confirmation is accepted', async () => {
    renderAgents();
    const dialog = await openRemoveFor('Workstation');

    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    expect(api.deleted).toEqual([]);
    expect(seams.clearedTokens).toEqual([]);
  });

  it('leaves the local state alone when the delete request fails', async () => {
    // A failed delete means the agent still exists on the relay; dropping its
    // token or its recents here would strand a browser that can still reach it,
    // which is what pins every cleanup to the success path.
    const { ApiError } = await import('../../api/errors');
    api.deleteError = new ApiError({
      status: 500,
      message: 'boom',
      code: null,
      retryAfterSeconds: null,
      detail: 'boom',
    });
    noteRecentPath('agent-1', '/srv/one');
    renderAgents();

    confirmRemove(await openRemoveFor('Workstation'));

    await waitFor(() => {
      expect(api.deleted).toEqual(['agent-1']);
    });
    expect(seams.clearedTokens).toEqual([]);
    expect(seams.disconnected).toEqual([]);
    expect(readRecentPaths('agent-1')).toEqual(['/srv/one']);
  });
});

describe('Agents list', () => {
  it('points an empty account at the pairing route', async () => {
    api.agents = [];

    renderAgents();

    expect(await screen.findByText(/No agents yet/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Pair an agent' })).toHaveAttribute(
      'href',
      '/pair',
    );
  });
});

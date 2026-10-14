import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation, type Location } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubLocalStorage } from '../../test/localStorageStub';
import type { Agent, AgentCredentials } from '../../api/agentRepository';
import {
  decodePairingFragment,
  encodePairingFragment,
} from '../../protocol/pairingPayload';
import { MALFORMED_PAYLOAD_MESSAGE } from '../pair/pairCopy';
import { INSTALL_COMMANDS } from './hostOs';
import { PAIRING_HINT_CODE_LABEL, PAIRING_HINT_LINK_LABEL } from './setupCopy';

const api = vi.hoisted((): {
  agents: Agent[];
  created: string[];
  deleted: string[];
  uninstallFlags: boolean[];
  reregistered: string[];
  credentials: AgentCredentials;
  createError: Error | null;
  deleteError: Error | null;
} => ({
  agents: [],
  created: [],
  deleted: [],
  uninstallFlags: [],
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
  deleteAgent: (agentId: string, uninstall = false) => {
    api.deleted.push(agentId);
    api.uninstallFlags.push(uninstall);
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
const { useToastStore } = await import('../../stores/toastStore');
const { Agents } = await import('./Agents');

const agent = (agentId: string, name: string): Agent => ({
  agentId,
  name,
  online: true,
  createdAt: '2026-08-04T10:00:00Z',
  lastSeenAt: null,
  agentVersion: '0.9.9',
  agentProtocolVersion: 7,
  supportsUninstall: false,
});

let client: QueryClient;

/**
 * Where the router ended up. The guide's paste box pairs by *navigating* to
 * `/pair#<fragment>`, so the destination — path and fragment both — is the
 * assertion, and `MemoryRouter` exposes it nowhere else.
 */
let location: Location | null = null;

function LocationProbe() {
  location = useLocation();
  return null;
}

const currentLocation = (): Location | null => location;

function renderAgents() {
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/agents']}>
        <Agents />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Fill the guide's paste box and submit it. */
function paste(text: string) {
  fireEvent.change(screen.getByLabelText('Pairing code or link'), {
    target: { value: text },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Pair' }));
}

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  api.agents = [agent('agent-1', 'Workstation'), agent('agent-2', 'Laptop')];
  api.created.length = 0;
  api.deleted.length = 0;
  api.uninstallFlags.length = 0;
  api.reregistered.length = 0;
  api.createError = null;
  api.deleteError = null;
  seams.clearedTokens.length = 0;
  seams.disconnected.length = 0;
  useSelectedAgentStore.setState({ selectedAgentId: null });
  useLastSessionStore.setState({ lastUsedSessionIds: {} });
  location = null;
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

describe('Agents remove and uninstall', () => {
  const UNINSTALL_LABEL = 'Also uninstall Mobaide from this computer';

  const openRemoveFor = async (name: string): Promise<HTMLElement> => {
    const row = (await screen.findByText(name)).closest('li') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: 'Remove' }));
    return screen.findByRole('alertdialog');
  };

  const confirmRemove = (dialog: HTMLElement) => {
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }));
  };

  beforeEach(() => {
    useToastStore.setState({ toasts: [] });
  });

  it('offers no uninstall option for a build that cannot uninstall itself', async () => {
    renderAgents();
    const dialog = await openRemoveFor('Workstation');

    expect(within(dialog).queryByLabelText(UNINSTALL_LABEL)).toBeNull();
    expect(dialog.textContent).toContain('The host keeps running');
    confirmRemove(dialog);

    await waitFor(() => {
      expect(api.deleted).toEqual(['agent-1']);
    });
    expect(api.uninstallFlags).toEqual([false]);
  });

  it('checks the uninstall option by default and sends it', async () => {
    api.agents = [{ ...agent('agent-1', 'Workstation'), supportsUninstall: true }];
    renderAgents();
    const dialog = await openRemoveFor('Workstation');

    const checkbox = within(dialog).getByLabelText(UNINSTALL_LABEL);
    expect((checkbox as HTMLInputElement).checked).toBe(true);
    expect(dialog.textContent).not.toContain('The host keeps running');
    confirmRemove(dialog);

    await waitFor(() => {
      expect(api.uninstallFlags).toEqual([true]);
    });
    await waitFor(() => {
      expect(useToastStore.getState().toasts.map((toast) => toast.message)).toEqual([
        '“Workstation” is uninstalling itself.',
      ]);
    });
  });

  it('removes only when the option is unchecked', async () => {
    api.agents = [{ ...agent('agent-1', 'Workstation'), supportsUninstall: true }];
    renderAgents();
    const dialog = await openRemoveFor('Workstation');

    fireEvent.click(within(dialog).getByLabelText(UNINSTALL_LABEL));
    expect(dialog.textContent).toContain('The host keeps running');
    confirmRemove(dialog);

    await waitFor(() => {
      expect(api.deleted).toEqual(['agent-1']);
    });
    expect(api.uninstallFlags).toEqual([false]);
    expect(useToastStore.getState().toasts).toEqual([]);
  });

  it('tells an offline agent it uninstalls on its next connect', async () => {
    api.agents = [
      { ...agent('agent-1', 'Workstation'), online: false, supportsUninstall: true },
    ];
    renderAgents();
    const dialog = await openRemoveFor('Workstation');

    expect(dialog.textContent).toContain('the next time it connects');
    confirmRemove(dialog);

    await waitFor(() => {
      expect(useToastStore.getState().toasts.map((toast) => toast.message)).toEqual([
        '“Workstation” will uninstall itself when it next comes online.',
      ]);
    });
  });
});

describe('Agents setup guide', () => {
  const renderEmpty = async () => {
    api.agents = [];
    renderAgents();
    await screen.findByRole('heading', { name: 'No agents yet' });
  };

  it('shows both install one-liners without opening anything', async () => {
    // The one thing a first-run user can act on, and it used to sit behind the
    // third of four equally weighted buttons.
    await renderEmpty();

    for (const entry of INSTALL_COMMANDS) {
      expect(screen.getByText(entry.command)).toBeInTheDocument();
    }
    expect(INSTALL_COMMANDS.length).toBe(2);
  });

  it('quotes what the agent prints, as an example rather than a control', async () => {
    await renderEmpty();
    const sample = screen.getByTestId('setup-example-output');

    expect(sample).toHaveTextContent(PAIRING_HINT_CODE_LABEL);
    expect(sample).toHaveTextContent(PAIRING_HINT_LINK_LABEL);
    expect(within(sample).queryByRole('button')).toBeNull();
  });

  it('hides the flat action row that the ordered steps replace', async () => {
    await renderEmpty();

    expect(screen.queryByRole('link', { name: 'Pair from a code' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Show install commands' })).toBeNull();
  });

  it('hands a pasted payload to /pair as the fragment the agent would print', async () => {
    // Navigating rather than pairing here: `/pair` owns the confirm → claim →
    // wait machine and the trust-pin ordering its tests pin.
    await renderEmpty();
    paste(`MOBA1|https://relay.test|nonce-abc|${'f'.repeat(64)}|1900000000`);

    await waitFor(() => {
      expect(currentLocation()?.pathname).toBe('/pair');
    });
    expect(decodePairingFragment(currentLocation()?.hash ?? '')).toBe(
      `MOBA1|https://relay.test|nonce-abc|${'f'.repeat(64)}|1900000000`,
    );
  });

  it('accepts the printed link as readily as the payload', async () => {
    // Both land on clipboards — the agent prints them on adjacent lines.
    const payload = `MOBA1|https://relay.test|nonce-abc|${'f'.repeat(64)}|1900000000`;
    await renderEmpty();
    paste(`https://app.mobaide.com/pair#${encodePairingFragment(payload)}`);

    await waitFor(() => {
      expect(currentLocation()?.pathname).toBe('/pair');
    });
    expect(decodePairingFragment(currentLocation()?.hash ?? '')).toBe(payload);
  });

  it('refuses to navigate on input that is not a pairing code', async () => {
    await renderEmpty();
    paste('just some words');

    expect(await screen.findByText(MALFORMED_PAYLOAD_MESSAGE)).toBeInTheDocument();
    expect(currentLocation()?.pathname).toBe('/agents');
  });

  it('still reaches the manual token path from the guide', async () => {
    await renderEmpty();
    fireEvent.click(screen.getByRole('button', { name: 'Add manually with a token' }));

    expect(screen.getByLabelText('Agent name')).toBeInTheDocument();
  });
});

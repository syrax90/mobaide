import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubLocalStorage } from '../../test/localStorageStub';

stubLocalStorage();

// No socket: the token mint resolving to null stops `WebSocketChannel` before it
// constructs one, the same seam the other screen tests use.
vi.mock('../../auth/agentTokens', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../auth/agentTokens')>();
  return { ...actual, ensureAgentToken: () => Promise.resolve(null) };
});

vi.mock('../../api/agentRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/agentRepository')>();
  return {
    ...actual,
    listAgents: () =>
      Promise.resolve([{ agentId: 'agent-1', name: 'dev box', online: true, version: null }]),
  };
});

const { autoscanChannelName } = await import('../../realtime/channels');
const { useConnectionStore } = await import('../../realtime/connectionStore');
const { AutoScanDetailScreen } = await import('./AutoScanDetailScreen');

let client: QueryClient;

/**
 * Render, then let the channel's async `buildUrl` settle. The mocked token mint
 * resolves in a microtask, and without flushing it React reports the resulting
 * state update as an un-acted one.
 */
async function renderScreen(path = '/agents/agent-1/autoscan/alpha') {
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/agents/:agentId/autoscan/:projectName"
            element={<AutoScanDetailScreen />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await act(async () => {
    await Promise.resolve();
  });
}

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});

afterEach(() => {
  cleanup();
  client.clear();
  useConnectionStore.setState({ channels: {} });
});

describe('AutoScanDetailScreen', () => {
  it('renders the pipeline in the fixed order before any frame arrives', async () => {
    await renderScreen();

    const phases = screen.getAllByRole('listitem').slice(0, 5);
    expect(phases.map((phase) => phase.textContent)).toEqual([
      'Scanpending',
      'Statepending',
      'Labelpending',
      'Worktreepending',
      'Spawnpending',
    ]);
  });

  it('shows the project from the path, decoded', async () => {
    await renderScreen('/agents/agent-1/autoscan/a%2Fb');

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('a/b');
  });

  it('waits for output rather than claiming an empty log', async () => {
    await renderScreen();

    expect(screen.getByText('Waiting for output…')).toBeInTheDocument();
    expect(screen.getByText('No issues processed yet.')).toBeInTheDocument();
  });

  it('reports the socket state until the agent describes the scan', async () => {
    await renderScreen();

    // No instance yet, so the chip falls back to the connection rather than
    // inventing a status.
    expect(screen.getAllByText('Connecting…').length).toBeGreaterThan(0);
  });

  it('explains a declined stream, which the reconnect loop cannot resolve', async () => {
    await renderScreen();

    act(() => {
      useConnectionStore.getState().setChannelState(autoscanChannelName('agent-1'), {
        status: 'connecting',
        attempt: 1,
        lastCloseCode: 1013,
      });
    });

    expect(screen.getByText(/turned down this log stream/)).toBeInTheDocument();
  });
});

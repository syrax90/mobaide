import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
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

// The Sessions list the screen watches to follow a scan started again elsewhere.
const api = vi.hoisted(() => ({ scans: [] as Record<string, unknown>[] }));

vi.mock('../../api/autoscanRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/autoscanRepository')>();
  return {
    ...actual,
    listAutoScans: () =>
      Promise.resolve({
        scans: api.scans,
        capacity: api.scans.length,
        max_scans: 5,
        available_projects: [],
        auto_scan_prompt: undefined,
      }),
    stopAutoScan: () => Promise.resolve(),
  };
});

function scan(startedAt: number) {
  return {
    project_name: 'alpha',
    status: 'idle',
    started_at: startedAt,
    last_activity_at: startedAt,
    processed_count: 0,
    scan_interval_seconds: 300,
    cycle_number: 1,
    current_seq: 0,
    source: 'linear',
    cli: 'claude',
    ui: 'terminal',
  };
}

const { autoscanChannelName } = await import('../../realtime/channels');
const { queryKeys } = await import('../../lib/queryKeys');
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
            path="/agents/:agentId/autoscan/:workspaceName"
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
  api.scans = [];
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});

afterEach(() => {
  cleanup();
  client.clear();
  useConnectionStore.setState({ channels: {} });
});

describe('AutoScanDetailScreen', () => {
  it('shows the workspace from the path, decoded', async () => {
    await renderScreen('/agents/agent-1/autoscan/a%2Fb');

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('a/b');
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

  it('follows a stopped scan that is started again, but not the row a Stop leaves behind', async () => {
    // The Sessions list already shows the run this screen is about to stop.
    api.scans = [scan(1_751_800_000)];
    client.setQueryData(queryKeys.autoscan('agent-1'), {
      scans: api.scans,
      capacity: 1,
      max_scans: 5,
      available_projects: [],
      auto_scan_prompt: undefined,
    });
    await renderScreen();
    const stop = await screen.findByRole('button', { name: 'Stop' });
    await waitFor(() => {
      expect(stop).toBeEnabled();
    });

    fireEvent.click(stop);

    // The list still lists the stopped run after the refetch: no restart.
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull();
    });
    await act(async () => {
      await client.refetchQueries({ queryKey: queryKeys.autoscan('agent-1') });
    });
    expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Run now' })).toBeNull();

    // Started again elsewhere: a new run, so the screen follows it live.
    api.scans = [scan(1_751_800_900)];
    await act(async () => {
      await client.refetchQueries({ queryKey: queryKeys.autoscan('agent-1') });
    });

    expect(await screen.findByRole('button', { name: 'Stop' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Run now' })).toBeInTheDocument();
  });
});

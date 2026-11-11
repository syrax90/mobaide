import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// The collaborator is the repository, not the wire. The display helpers stay
// real: they decide the chip, the countdown and the row order.
const api = vi.hoisted(() => ({
  scans: [] as Record<string, unknown>[],
  stops: [] as string[],
  runs: [] as string[],
}));

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
    stopAutoScan: (_agentId: string, projectName: string) => {
      api.stops.push(projectName);
      return Promise.resolve();
    },
    runAutoScanNow: (_agentId: string, projectName: string) => {
      api.runs.push(projectName);
      return Promise.resolve({ ok: true, project_name: projectName, next_scan_at: null });
    },
  };
});

const { AutoScanSection } = await import('./AutoScanSection');

function scan(projectName: string, overrides: Record<string, unknown> = {}) {
  return {
    project_name: projectName,
    status: 'scanning',
    started_at: 1_751_800_000,
    last_activity_at: 1_751_800_000,
    processed_count: 2,
    scan_interval_seconds: 300,
    cycle_number: 3,
    current_seq: 12,
    ...overrides,
  };
}

let client: QueryClient;

const renderSection = (online = true) =>
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AutoScanSection agentId="agent-1" online={online} />
      </MemoryRouter>
    </QueryClientProvider>,
  );

beforeEach(() => {
  api.scans = [];
  api.stops = [];
  api.runs = [];
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});

afterEach(() => {
  cleanup();
  client.clear();
});

describe('AutoScanSection', () => {
  it('renders nothing while the agent is offline, without asking the relay', () => {
    api.scans = [scan('alpha')];
    const { container } = renderSection(false);
    // A disabled query, so the relay's 503-for-an-offline-agent never fires the
    // global error toast.
    expect(container).toBeEmptyDOMElement();
  });

  it('lists running scans with their status and links to the monitor', async () => {
    api.scans = [scan('alpha'), scan('beta', { status: 'error', last_activity_at: 1_751_900_000 })];
    renderSection();

    // Newest activity first.
    const links = await screen.findAllByRole('link');
    expect(links.map((link) => link.textContent)).toEqual(['beta', 'alpha']);
    expect(links[0]).toHaveAttribute('href', '/agents/agent-1/autoscan/beta');
    expect(screen.getByText('Error')).toBeInTheDocument();
    expect(screen.getByText('Scanning')).toBeInTheDocument();
  });

  it('percent-encodes a project name in the monitor link', async () => {
    api.scans = [scan('a/b')];
    renderSection();

    expect(await screen.findByRole('link', { name: 'a/b' })).toHaveAttribute(
      'href',
      '/agents/agent-1/autoscan/a%2Fb',
    );
  });

  it('stops and re-runs the scan the button belongs to', async () => {
    api.scans = [scan('alpha'), scan('beta')];
    renderSection();

    const [alpha, beta] = await screen.findAllByRole('listitem');
    fireEvent.click(within(beta!).getByRole('button', { name: 'Stop' }));
    fireEvent.click(within(alpha!).getByRole('button', { name: 'Run now' }));

    await waitFor(() => {
      expect(api.stops).toEqual(['beta']);
    });
    expect(api.runs).toEqual(['alpha']);
  });
});

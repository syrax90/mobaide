import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubLocalStorage } from '../../test/localStorageStub';
import type { Agent } from '../../api/agentRepository';

interface ClaimResult {
  agentId: string;
  agentFriendlyName: string;
  wsDelivered: boolean | null;
}

const api = vi.hoisted((): {
  agents: Agent[];
  claims: { nonce: string; trustKeyFp: string }[];
  claimResult: ClaimResult;
  claimError: Error | null;
} => ({
  agents: [],
  claims: [],
  claimResult: {
    agentId: 'agent-1',
    agentFriendlyName: 'Workstation',
    wsDelivered: null,
  },
  claimError: null,
}));

vi.mock('../../api/agentRepository', () => ({
  listAgents: () => Promise.resolve(api.agents),
  claimPairing: (nonce: string, trustKeyFp: string) => {
    api.claims.push({ nonce, trustKeyFp });
    const failure = api.claimError;
    if (failure !== null) return Promise.reject(failure);
    return Promise.resolve(api.claimResult);
  },
}));

// Same stand-in as `SessionsScreen.test.tsx`: Node >= 22 defines an
// experimental global `localStorage` that is undefined without
// --localstorage-file and shadows jsdom's, so the persisted stores would throw
// on their first write. Installed before the stores are imported.
// Before any persisted store is imported.
stubLocalStorage();

const { useSessionStore } = await import('../../auth/sessionStore');
const { useSelectedAgentStore } = await import('../../stores/selectedAgentStore');
const { useTrustPinStore } = await import('../../stores/trustPinStore');
const { Pair } = await import('./Pair');

const FP = 'f'.repeat(64);
const NONCE = 'nonce-abc';
const HANDOFF_KEY = 'mobaide-pairing';

/** Well clear of the 60 s wait cap, so no test races the expiry check. */
const futureExp = () => Math.floor(Date.now() / 1000) + 3600;

const payload = (
  over: Partial<{ relay: string; nonce: string; fp: string; exp: number }> = {},
) =>
  [
    'MOBA1',
    over.relay ?? 'https://relay.test',
    over.nonce ?? NONCE,
    over.fp ?? FP,
    String(over.exp ?? futureExp()),
  ].join('|');

const fragmentFor = (text: string) =>
  btoa(text).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '');

const agent = (agentId: string, online: boolean): Agent => ({
  agentId,
  name: 'Workstation',
  online,
  createdAt: '2026-08-04T10:00:00Z',
  lastSeenAt: null,
  agentVersion: null,
  agentProtocolVersion: null,
});

function Probe() {
  const location = useLocation();
  return (
    <span data-testid="location">{`${location.pathname}${location.search}${location.hash}`}</span>
  );
}

const currentLocation = (): string | null => screen.getByTestId('location').textContent;

let client: QueryClient;

function renderPair(initialEntry = '/pair') {
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Probe />
        <Pair />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  api.agents = [agent('agent-1', true)];
  api.claims.length = 0;
  api.claimResult = {
    agentId: 'agent-1',
    agentFriendlyName: 'Workstation',
    wsDelivered: null,
  };
  api.claimError = null;
  useSessionStore.setState({ authState: { kind: 'userOnly' } });
  useTrustPinStore.setState({ pins: {} });
  useSelectedAgentStore.setState({ selectedAgentId: null });
  sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  client.clear();
  localStorage.clear();
});

describe('Pair deep link', () => {
  it('reads the fragment and scrubs it out of the URL', async () => {
    renderPair(`/pair#${fragmentFor(payload())}`);

    expect(await screen.findByText('Pair this agent?')).toBeInTheDocument();
    // A `replace` navigation, so Back cannot put the nonce back in the address
    // bar — `location.hash = ''` would push a history entry that does exactly
    // that.
    await waitFor(() => {
      expect(currentLocation()).toBe('/pair');
    });
  });

  it('holds a deep-linked payload across the sign-in bounce', async () => {
    useSessionStore.setState({ authState: { kind: 'unauthenticated' } });

    const signedOut = renderPair(`/pair#${fragmentFor(payload())}`);
    await waitFor(() => {
      expect(currentLocation()).toBe('/login?next=%2Fpair');
    });
    // The fragment never survives a guard redirect, so the route stashes it
    // itself before bouncing.
    expect(sessionStorage.getItem(HANDOFF_KEY)).toBe(payload());
    signedOut.unmount();

    useSessionStore.setState({ authState: { kind: 'userOnly' } });
    renderPair();

    expect(await screen.findByText('Pair this agent?')).toBeInTheDocument();
  });

  it('consumes the stash on read so a single-use nonce is not retried', async () => {
    sessionStorage.setItem(HANDOFF_KEY, payload());

    renderPair();

    expect(await screen.findByText('Pair this agent?')).toBeInTheDocument();
    expect(sessionStorage.getItem(HANDOFF_KEY)).toBeNull();
  });

  it('falls through to the paste box when the fragment is not a payload', async () => {
    renderPair('/pair#not-a-payload');

    expect(await screen.findByLabelText('Pairing code')).toBeInTheDocument();
    await waitFor(() => {
      expect(currentLocation()).toBe('/pair');
    });
  });
});

describe('Pair paste box', () => {
  const paste = (text: string) => {
    fireEvent.change(screen.getByLabelText('Pairing code'), { target: { value: text } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
  };

  it('accepts a payload carrying the newlines of a wrapped terminal copy', async () => {
    renderPair();
    const wrapped = `MOBA1|https://relay.test|non\nce-abc|${FP}|${String(futureExp())}`;

    paste(wrapped);

    expect(await screen.findByText('Pair this agent?')).toBeInTheDocument();
    expect(screen.getByText('https://relay.test')).toBeInTheDocument();
  });

  it('explains a malformed code without mentioning a scanner', async () => {
    renderPair();

    paste('MOBA9|nope');

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'That is not a Mobaide pairing code',
    );
    expect(api.claims).toEqual([]);
  });

  it('says how stale an expired code is instead of calling it malformed', async () => {
    renderPair();

    paste(payload({ exp: Math.floor(Date.now() / 1000) - 3 * 60 }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('expired 3 minutes ago');
    expect(api.claims).toEqual([]);
  });

  it('refuses a changed trust key with no override affordance', async () => {
    useTrustPinStore.setState({ pins: { 'https://relay.test': 'a'.repeat(64) } });
    renderPair();

    paste(payload());

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('possible MITM');
    expect(api.claims).toEqual([]);
    // Only "Try again" and "All agents" — nothing that claims anyway.
    expect(screen.queryByRole('button', { name: /pair anyway|continue|override/i })).toBeNull();
  });

  it('does not pin a relay merely because a link was opened', async () => {
    renderPair();

    paste(payload());

    // Reaching the confirmation step must leave no trace. A pairing link is
    // attacker-authorable and needs no interaction beyond opening it, so a pin
    // written here could be planted — and since a mismatch is a hard refusal
    // with no override, that would lock this browser out of pairing entirely.
    expect(await screen.findByText('Pair this agent?')).toBeInTheDocument();
    expect(useTrustPinStore.getState().pins).toEqual({});
  });

  it('pins a first-seen relay once the user confirms', async () => {
    renderPair();

    paste(payload());
    fireEvent.click(await screen.findByRole('button', { name: 'Pair' }));

    expect(useTrustPinStore.getState().pins).toEqual({ 'https://relay.test': FP });
  });

  it('tells the user to compare the code against the agent host', async () => {
    // RFC 8628 §5.4: a link works for whoever opens it, and an attacker's
    // payload against the real relay looks identical on this screen. The code
    // prefix is the only field that separates them, so the copy has to say so.
    renderPair();

    paste(payload());

    expect(await screen.findByText(/Check those first characters match/)).toBeInTheDocument();
  });
});

describe('Pair claim', () => {
  const confirmDeepLink = async () => {
    renderPair(`/pair#${fragmentFor(payload())}`);
    fireEvent.click(await screen.findByRole('button', { name: 'Pair' }));
  };

  it('succeeds immediately when the agent is already online at claim time', async () => {
    // The claim's own refetch seeds an online agent, so the watcher resolves
    // on its first read and the user never sees the waiting screen settle.
    api.agents = [agent('agent-1', true)];

    await confirmDeepLink();

    expect(await screen.findByText(/paired and online/)).toBeInTheDocument();
    expect(api.claims).toEqual([{ nonce: NONCE, trustKeyFp: FP }]);
  });

  it('points the sessions list at the agent it just paired', async () => {
    await confirmDeepLink();

    await waitFor(() => {
      expect(useSelectedAgentStore.getState().selectedAgentId).toBe('agent-1');
    });
  });

  it('waits, then succeeds when the agent comes online on a later refetch', async () => {
    api.agents = [agent('agent-1', false)];

    await confirmDeepLink();
    expect(await screen.findByText(/Waiting for/)).toBeInTheDocument();

    // What `agent.status_changed` does through the query bridge: invalidate
    // `['agents']`, which refetches the query this screen has mounted.
    api.agents = [agent('agent-1', true)];
    await client.invalidateQueries({ queryKey: ['agents'] });

    expect(await screen.findByText(/paired and online/)).toBeInTheDocument();
  });

  it('gives up after the timeout without claiming the agent is paired', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.agents = [agent('agent-1', false)];
    api.claimResult = { ...api.claimResult, wsDelivered: null };

    await confirmDeepLink();
    await screen.findByText(/Waiting for/);

    await vi.advanceTimersByTimeAsync(61_000);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('did not come online within 60 s');
    // `null` ws_delivered appends nothing — never the `false` sentence.
    expect(alert).not.toHaveTextContent('staged');
  });

  it('shows the remedy copy for a consumed nonce', async () => {
    const { ApiError } = await import('../../api/errors');
    api.claimError = new ApiError({
      status: 410,
      message: 'gone',
      code: null,
      retryAfterSeconds: null,
      detail: 'gone',
    });

    await confirmDeepLink();

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Pairing code consumed or expired.',
    );
  });

  it('does not claim until the user confirms the relay', async () => {
    // The confirmation names the relay: it is the phishing mitigation for a
    // clicked link exactly as it is for a scanned QR.
    renderPair(`/pair#${fragmentFor(payload())}`);

    expect(await screen.findByText('Pair this agent?')).toBeInTheDocument();
    expect(api.claims).toEqual([]);

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(await screen.findByLabelText('Pairing code')).toBeInTheDocument();
    expect(api.claims).toEqual([]);
  });
});

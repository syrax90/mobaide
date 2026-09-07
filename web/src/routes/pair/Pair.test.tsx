import { QueryClient, QueryClientProvider, focusManager } from '@tanstack/react-query';
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
  listCalls: number;
  claims: { nonce: string; trustKeyFp: string }[];
  claimResult: ClaimResult;
  claimError: Error | null;
} => ({
  agents: [],
  listCalls: 0,
  claims: [],
  claimResult: {
    agentId: 'agent-1',
    agentFriendlyName: 'Workstation',
    wsDelivered: null,
  },
  claimError: null,
}));

vi.mock('../../api/agentRepository', () => ({
  listAgents: () => {
    api.listCalls += 1;
    return Promise.resolve(api.agents);
  },
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
  api.listCalls = 0;
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
  focusManager.setFocused(undefined);
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

  it('still pairs a signed-in visitor when session storage is denied', async () => {
    // Safari's "block all cookies", a partitioned context, a locked-down
    // profile: `setItem` throws. The scrub takes the fragment either way, so
    // without the in-memory arm the nonce is destroyed and the visitor is left
    // on an empty paste box with nothing left to paste.
    // `Object.getPrototypeOf(sessionStorage)`, never the `Storage` global:
    // Node >= 22 ships its own experimental `Storage` that shadows jsdom's, so
    // spying on `Storage.prototype` here silently intercepts nothing and the
    // test passes against the unfixed code.
    const setItem = vi
      .spyOn(Object.getPrototypeOf(sessionStorage) as Storage, 'setItem')
      .mockImplementation(() => {
        throw new DOMException('denied', 'SecurityError');
      });

    try {
      renderPair(`/pair#${fragmentFor(payload())}`);

      expect(await screen.findByText('Pair this agent?')).toBeInTheDocument();
    } finally {
      setItem.mockRestore();
    }
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

  /** The claim's refetch and the watcher's one-shot refetch have both landed. */
  const agentsQuerySettled = () =>
    waitFor(() => {
      expect(client.getQueryState(['agents'])?.fetchStatus).toBe('idle');
    });

  it('reports "not online yet" after the timeout, as a status rather than a failure', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.agents = [agent('agent-1', false)];
    api.claimResult = { ...api.claimResult, wsDelivered: null };

    await confirmDeepLink();
    await screen.findByText(/Waiting for/);

    await vi.advanceTimersByTimeAsync(61_000);

    // The claim succeeded and the agent is paired; the wait running out is
    // not an error, so no `alert` and no "Pairing failed".
    const heading = await screen.findByRole('heading', { name: 'Agent not online yet' });
    const panel = heading.closest('[role="status"]');
    expect(panel).not.toBeNull();
    expect(panel).toHaveTextContent('paired to your account');
    // `null` ws_delivered appends nothing — never the `false` sentence.
    expect(panel).not.toHaveTextContent('staged');
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.queryByText(/Pairing failed/)).toBeNull();
    expect(screen.getByRole('button', { name: 'Keep waiting' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'All agents' })).toBeInTheDocument();
  });

  it('recovers through the 5 s resync when no status event ever arrives', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.agents = [agent('agent-1', false)];

    await confirmDeepLink();
    await screen.findByText(/Waiting for/);
    await agentsQuerySettled();
    const listCallsBefore = api.listCalls;
    // The relay now routes the agent, but the `agent.status_changed` push
    // was lost — nothing invalidates `['agents']`.
    api.agents = [agent('agent-1', true)];

    await vi.advanceTimersByTimeAsync(5_500);

    expect(await screen.findByText(/paired and online/)).toBeInTheDocument();
    expect(api.listCalls).toBeGreaterThan(listCallsBefore);
  });

  it('keeps resyncing while the tab is in the background', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.agents = [agent('agent-1', false)];

    await confirmDeepLink();
    await screen.findByText(/Waiting for/);
    await agentsQuerySettled();
    // Pairing is the flow where the user leaves this tab for a terminal.
    // TanStack pauses interval refetches in an unfocused tab by default.
    focusManager.setFocused(false);
    api.agents = [agent('agent-1', true)];

    await vi.advanceTimersByTimeAsync(5_500);

    expect(await screen.findByText(/paired and online/)).toBeInTheDocument();
  });

  it('restarts the clock from zero on "Keep waiting"', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.agents = [agent('agent-1', false)];

    await confirmDeepLink();
    await screen.findByText(/Waiting for/);
    await vi.advanceTimersByTimeAsync(61_000);
    await screen.findByRole('heading', { name: 'Agent not online yet' });

    fireEvent.click(screen.getByRole('button', { name: 'Keep waiting' }));

    expect(await screen.findByText(/Waiting for/)).toBeInTheDocument();
    expect(screen.getByText(/Elapsed 0s/)).toBeInTheDocument();

    // Half-way through a fresh 60 s budget it is still counting, not timed
    // out again at once from the previous attempt's elapsed count.
    await vi.advanceTimersByTimeAsync(30_500);
    expect(screen.getByText(/Elapsed 3[01]s/)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Agent not online yet' })).toBeNull();

    api.agents = [agent('agent-1', true)];
    await client.invalidateQueries({ queryKey: ['agents'] });

    expect(await screen.findByText(/paired and online/)).toBeInTheDocument();
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

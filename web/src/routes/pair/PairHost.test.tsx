import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../api/errors';
import { stubLocalStorage } from '../../test/localStorageStub';
import type {
  Agent,
  DeviceCodeMint,
  DeviceCodeStatus,
} from '../../api/agentRepository';

interface ClaimResult {
  agentId: string;
  agentFriendlyName: string;
  wsDelivered: boolean | null;
}

const api = vi.hoisted((): {
  agents: Agent[];
  mint: DeviceCodeMint;
  mintError: Error | null;
  status: DeviceCodeStatus;
  confirmResult: ClaimResult;
  confirmError: Error | null;
  canceled: string[];
} => ({
  agents: [],
  mint: {
    requestId: 'req-1',
    code: 'WDJB-MJHT',
    expiresAt: '2099-01-01T00:00:00Z',
    verificationCommand: 'mobaide-agent pair WDJB-MJHT',
  },
  mintError: null,
  status: {
    status: 'pending',
    expiresAt: '2099-01-01T00:00:00Z',
    hostname: null,
    os: null,
    isRepair: false,
    repairAgentName: null,
    blockedReason: null,
  },
  confirmResult: {
    agentId: 'agent-1',
    agentFriendlyName: 'Workstation',
    wsDelivered: true,
  },
  confirmError: null,
  canceled: [],
}));

vi.mock('../../api/agentRepository', () => ({
  listAgents: () => Promise.resolve(api.agents),
  mintDeviceCode: () =>
    api.mintError !== null ? Promise.reject(api.mintError) : Promise.resolve(api.mint),
  getDeviceCodeStatus: () => Promise.resolve(api.status),
  confirmDeviceCode: () =>
    api.confirmError !== null
      ? Promise.reject(api.confirmError)
      : Promise.resolve(api.confirmResult),
  cancelDeviceCode: (requestId: string) => {
    api.canceled.push(requestId);
    return Promise.resolve();
  },
}));

// Before any persisted store is imported — same reason as `Pair.test.tsx`.
stubLocalStorage();

const { useSelectedAgentStore } = await import('../../stores/selectedAgentStore');
const { PairHost } = await import('./PairHost');

const agent = (agentId: string, online: boolean): Agent => ({
  agentId,
  name: 'Workstation',
  online,
  createdAt: '2026-08-04T10:00:00Z',
  lastSeenAt: null,
  agentVersion: null,
  agentProtocolVersion: null,
});

const redeemed = (over: Partial<DeviceCodeStatus> = {}): DeviceCodeStatus => ({
  status: 'redeemed',
  expiresAt: '2099-01-01T00:00:00Z',
  hostname: 'pavel-mbp',
  os: 'Darwin',
  isRepair: false,
  repairAgentName: null,
  blockedReason: null,
  ...over,
});

let client: QueryClient;

function renderPairHost() {
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/agents/pair']}>
        <PairHost />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const getCode = () => {
  fireEvent.click(screen.getByRole('button', { name: 'Get a pairing code' }));
};

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  api.agents = [agent('agent-1', true)];
  api.mintError = null;
  api.status = {
    status: 'pending',
    expiresAt: '2099-01-01T00:00:00Z',
    hostname: null,
    os: null,
    isRepair: false,
    repairAgentName: null,
    blockedReason: null,
  };
  api.confirmResult = {
    agentId: 'agent-1',
    agentFriendlyName: 'Workstation',
    wsDelivered: true,
  };
  api.confirmError = null;
  api.canceled.length = 0;
  useSelectedAgentStore.setState({ selectedAgentId: null });
});

afterEach(() => {
  cleanup();
  client.clear();
  localStorage.clear();
});

describe('PairHost minting', () => {
  it('shows the command to run rather than only the bare code', async () => {
    renderPairHost();

    getCode();

    // The operator is at a terminal, so the copyable artefact has to be the
    // whole command; the code alone leaves them guessing the verb.
    expect(await screen.findByTestId('device-code-command')).toHaveTextContent(
      'mobaide-agent pair WDJB-MJHT',
    );
    expect(screen.getByText('WDJB-MJHT')).toBeInTheDocument();
  });

  it('warns that entering the code hands this account the machine', async () => {
    renderPairHost();

    getCode();

    expect(await screen.findByTestId('device-code-command')).toBeInTheDocument();
    expect(screen.getByText(/only enter the code on a machine you own/i)).toBeInTheDocument();
  });

  it('reports a mint failure without stranding the user', async () => {
    api.mintError = new ApiError({
      status: 503,
      message: 'no keys',
      code: null,
      retryAfterSeconds: null,
      detail: 'no keys',
    });
    renderPairHost();

    getCode();

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'not configured on this relay',
    );
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });
});

describe('PairHost confirmation', () => {
  it('names the machine that entered the code', async () => {
    api.status = redeemed();
    renderPairHost();

    getCode();

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('pavel-mbp');
    expect(dialog).toHaveTextContent('Darwin');
    // Named, but explicitly not trusted: hostname and os come from the
    // anonymous redeem body, so the dialog has to say what it does not know.
    expect(dialog).toHaveTextContent('do not continue');
  });

  it('swaps the heading to a move warning when the code claims an owned agent', async () => {
    api.status = redeemed({ repairAgentName: 'old-mbp' });
    renderPairHost();

    getCode();

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('Move ‘old-mbp’ to this machine?');
    expect(dialog).toHaveTextContent('rotates that agent');
  });

  it('asks the first-pair question when the code claims no existing agent', async () => {
    api.status = redeemed({ repairAgentName: null });
    renderPairHost();

    getCode();

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('Is this your machine?');
  });

  it('refuses to confirm a machine owned by another account', async () => {
    api.status = redeemed({ isRepair: true, blockedReason: 'owned_by_other' });
    renderPairHost();

    getCode();

    // Surfaced before the click, because confirming would burn both the
    // code and the nonce on a refusal that was knowable in advance.
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'already paired to a different account',
    );
    expect(screen.getByRole('button', { name: 'Pair this machine' })).toBeDisabled();
  });

  it('explains a stale agent id instead of blaming the account', async () => {
    api.status = redeemed({ isRepair: true, blockedReason: 'unknown_agent' });
    renderPairHost();

    getCode();

    expect(await screen.findByRole('alert')).toHaveTextContent('re-pair --fresh');
  });

  it('waits for the agent to come online before declaring success', async () => {
    api.status = redeemed();
    api.agents = [agent('agent-1', false)];
    renderPairHost();
    getCode();
    await screen.findByRole('dialog');

    fireEvent.click(screen.getByRole('button', { name: 'Pair this machine' }));

    // A 200 from confirm only proves the relay stored credentials; the agent
    // still has to pick them up and connect back.
    expect(
      await screen.findByText(/Waiting for .Workstation. to come online/),
    ).toBeInTheDocument();
    expect(useSelectedAgentStore.getState().selectedAgentId).toBeNull();
  });

  it('selects the agent once it reports online', async () => {
    api.status = redeemed();
    renderPairHost();
    getCode();
    await screen.findByRole('dialog');

    fireEvent.click(screen.getByRole('button', { name: 'Pair this machine' }));

    expect(await screen.findByText(/paired and online/)).toBeInTheDocument();
    await waitFor(() => {
      expect(useSelectedAgentStore.getState().selectedAgentId).toBe('agent-1');
    });
  });

  it('shows the account-pin refusal with no override affordance', async () => {
    api.status = redeemed();
    api.confirmError = new ApiError({
      status: 409,
      message: 'raw detail',
      code: 'trust_key_pin_mismatch',
      retryAfterSeconds: null,
      detail: { error: 'trust_key_pin_mismatch' },
    });
    renderPairHost();
    getCode();
    await screen.findByRole('dialog');

    fireEvent.click(screen.getByRole('button', { name: 'Pair this machine' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('show-trust-fp');
    expect(
      screen.queryByRole('button', { name: /pair anyway|continue|override/i }),
    ).toBeNull();
  });
});

describe('PairHost abandonment', () => {
  it('releases the agent waiting on the nonce when the user cancels', async () => {
    api.status = redeemed();
    renderPairHost();
    getCode();
    await screen.findByRole('dialog');

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    // A cancel expires the nonce server-side, which is what stops the
    // agent's WebSocket hanging for the rest of the window.
    expect(api.canceled).toEqual(['req-1']);
    expect(
      await screen.findByRole('button', { name: 'Get a pairing code' }),
    ).toBeInTheDocument();
  });

  it('reports a code that expired before any machine used it', async () => {
    api.status = { ...redeemed(), status: 'expired', hostname: null, os: null };
    renderPairHost();

    getCode();

    expect(await screen.findByRole('alert')).toHaveTextContent('That code expired');
  });
});

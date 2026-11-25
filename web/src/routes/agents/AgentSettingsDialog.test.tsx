import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Agent } from '../../api/agentRepository';
import { agentSettingsSchema, type AgentSettings } from '../../protocol/dto/agentSettings';
import { agentVersionSchema, type AgentVersionInfo } from '../../protocol/dto/agentVersion';
import { VOICE_UNREACHABLE_POLL_MS } from '../../voice/useVoiceStatus';
import { AgentSettingsDialog } from './AgentSettingsDialog';

vi.mock('../../realtime/useAgentEvents', () => ({
  useAgentEvents: () => undefined,
}));

/** The agent's answers, and how often it was asked. */
const agentApi = vi.hoisted(
  (): {
    version: () => Promise<AgentVersionInfo>;
    settings: () => Promise<AgentSettings>;
    probes: number;
    reads: number;
  } => ({
    version: () => Promise.reject(new Error('set by each test')),
    settings: () => Promise.reject(new Error('set by each test')),
    probes: 0,
    reads: 0,
  }),
);

vi.mock('../../api/agentCapabilities', () => ({
  fetchAgentCapabilities: (): Promise<AgentVersionInfo> => {
    agentApi.probes += 1;
    return agentApi.version();
  },
}));

vi.mock('../../api/agentSettingsRepository', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/agentSettingsRepository')>()),
  getAgentSettings: (): Promise<AgentSettings> => {
    agentApi.reads += 1;
    return agentApi.settings();
  },
}));

const AGENT: Agent = {
  agentId: 'a1',
  name: 'laptop',
  online: true,
  createdAt: '2026-11-01T00:00:00Z',
  lastSeenAt: null,
  agentVersion: null,
  agentProtocolVersion: null,
  supportsUninstall: false,
};

const WITH_KEEP_AWAKE = agentVersionSchema.parse({
  name: 'mobaide-agent',
  server_version: '1.5.0',
  protocol_versions: [7],
  min_client_version: 1,
  capabilities: { agent_settings: { keep_awake: true } },
});

const OFF = agentSettingsSchema.parse({
  keep_awake: { enabled: false, state: 'off', message: null },
});

const UNREACHABLE = `Couldn’t reach ${AGENT.name}. Is it online?`;
const CHECKING = `Checking ${AGENT.name}…`;
const SETTING = `Keep ${AGENT.name} awake`;

const offline = (): Promise<never> => Promise.reject(new Error('the agent is offline'));
const noAnswer = (): Promise<never> => new Promise<never>(() => undefined);
/** From here on the agent answers. */
const comeBack = () => {
  agentApi.version = () => Promise.resolve(WITH_KEEP_AWAKE);
  agentApi.settings = () => Promise.resolve(OFF);
};

let client: QueryClient;

const dialog = (agent: Agent) => (
  <QueryClientProvider client={client}>
    <AgentSettingsDialog agent={agent} onClose={() => undefined} />
  </QueryClientProvider>
);
const advance = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

beforeEach(() => {
  vi.useFakeTimers();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  comeBack();
  agentApi.probes = 0;
  agentApi.reads = 0;
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('AgentSettingsDialog for an agent that did not answer', () => {
  it.each([
    [
      'the capability probe',
      () => {
        agentApi.version = offline;
      },
    ],
    [
      'the settings read',
      () => {
        agentApi.settings = offline;
      },
    ],
  ])('asks again by itself after %s failed, then stops asking', async (_label, goAway) => {
    goAway();
    render(dialog(AGENT));
    await advance(1);
    expect(screen.getByRole('alert')).toHaveTextContent(UNREACHABLE);
    expect(screen.queryByRole('checkbox')).toBeNull();

    comeBack();
    await advance(VOICE_UNREACHABLE_POLL_MS);
    // After a failed probe the settings are read only once it answers.
    await advance(1);

    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByRole('checkbox', { name: SETTING })).not.toBeChecked();
    const asked = { probes: agentApi.probes, reads: agentApi.reads };
    await advance(VOICE_UNREACHABLE_POLL_MS * 3);
    expect({ probes: agentApi.probes, reads: agentApi.reads }).toEqual(asked);
  });

  it('asks again at once when the agent comes back online after a probe with no answer', async () => {
    // A request with no answer never fails, so only the agent list can end it.
    agentApi.version = noAnswer;
    const view = render(dialog({ ...AGENT, online: false }));
    await advance(1);
    expect(screen.getByRole('status')).toHaveTextContent(CHECKING);

    comeBack();
    view.rerender(dialog({ ...AGENT, online: true }));
    // Far less than the retry interval: the agent list's change did this.
    await advance(1);

    expect(screen.queryByText(CHECKING)).toBeNull();
    expect(screen.getByRole('checkbox', { name: SETTING })).toBeInTheDocument();
  });
});

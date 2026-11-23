import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Agent } from '../../api/agentRepository';
import type { AgentVersionInfo } from '../../protocol/dto/agentVersion';
import type { VoiceStatus } from '../../protocol/dto/voice';
import { stubLocalStorage } from '../../test/localStorageStub';

// Before importing the store, so zustand's
// `createJSONStorage(() => window.localStorage)` captures the stand-in.
stubLocalStorage();

vi.mock('../../realtime/useAgentEvents', () => ({
  useAgentEvents: () => undefined,
}));

/** The agent's answers. The seeded cache serves most tests, so by default it is never asked. */
const agentApi = vi.hoisted(
  (): {
    version: () => Promise<AgentVersionInfo>;
    voiceStatus: () => Promise<VoiceStatus>;
    probes: number;
  } => ({
    version: () => Promise.reject(new Error('the capability comes from the seeded cache')),
    voiceStatus: () => Promise.reject(new Error('the status comes from the seeded cache')),
    probes: 0,
  }),
);

vi.mock('../../api/agentCapabilities', () => ({
  fetchAgentCapabilities: (): Promise<AgentVersionInfo> => {
    agentApi.probes += 1;
    return agentApi.version();
  },
}));

vi.mock('../../api/voiceRepository', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/voiceRepository')>()),
  getVoiceStatus: (): Promise<VoiceStatus> => agentApi.voiceStatus(),
}));

const { queryKeys } = await import('../../lib/queryKeys');
const { agentVersionSchema } = await import('../../protocol/dto/agentVersion');
const { cloudVoiceSchema } = await import('../../protocol/dto/cloudVoice');
const { voiceStatusSchema } = await import('../../protocol/dto/voice');
const { localBlocker } = await import('../../voice/VoiceEngineChooser');
const { LOCAL_VOICE_NAME } = await import('../../voice/cloudVoiceCopy');
const { VOICE_UNREACHABLE_POLL_MS } = await import('../../voice/useVoiceStatus');
const { VoiceSettingsDialog } = await import('./VoiceSettingsDialog');

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

const NOT_INSTALLED = voiceStatusSchema.parse({
  state: 'not_installed',
  enabled: false,
  repairing: false,
  model: {
    id: 'parakeet',
    name: 'Parakeet',
    languages: ['en'],
    download_bytes: 670_000_000,
    disk_bytes: 700_000_000,
    ram_bytes: 1_200_000_000,
    notice: 'CC-BY-4.0',
  },
  progress: null,
  host: { recommendation: 'ok', reasons: [] },
  speed_factor: null,
  error: null,
});

const cloud = (engines: Record<string, string>) =>
  cloudVoiceSchema.parse({
    state: 'ready',
    limits: { sample_rate: 16000, max_seconds: 120, max_chunk_bytes: 65536 },
    usage: { used_seconds: 0, limit_seconds: 1800, resets_at: '2026-11-23T00:00:00Z' },
    engines,
  });

const WITH_VOICE = agentVersionSchema.parse({
  name: 'mobaide-agent',
  server_version: '1.4.0',
  protocol_versions: [7],
  min_client_version: 1,
  capabilities: {
    voice: { dictation: true, sample_rate: 16000, max_seconds: 120, max_chunk_bytes: 65536 },
  },
});

let client: QueryClient;

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(queryKeys.agentCapabilities(AGENT.agentId), WITH_VOICE);
  client.setQueryData(queryKeys.voiceStatus(AGENT.agentId), NOT_INSTALLED);
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
});

describe('VoiceSettingsDialog before an engine is chosen', () => {
  it('offers both engines unpicked, and the on-computer install only once that engine is chosen', async () => {
    client.setQueryData(queryKeys.cloudVoice(), cloud({}));
    render(
      <QueryClientProvider client={client}>
        <VoiceSettingsDialog agent={AGENT} onClose={() => undefined} />
      </QueryClientProvider>,
    );

    const engines = screen.getAllByRole('radio');
    expect(engines).toHaveLength(2);
    for (const engine of engines) expect(engine).not.toBeChecked();
    // Showing the install here would steer the user away from the
    // recommended engine before they picked one.
    expect(screen.queryByRole('button', { name: 'Enable voice input' })).toBeNull();
    expect(screen.getByRole('checkbox', { name: 'Show the mic button in sessions' })).toBeChecked();

    act(() => {
      client.setQueryData(queryKeys.cloudVoice(), cloud({ [AGENT.agentId]: 'local' }));
    });

    expect(await screen.findByRole('button', { name: 'Enable voice input' })).toBeInTheDocument();
    expect(screen.getAllByRole('radio').map((engine) => (engine as HTMLInputElement).checked)).toEqual([
      false,
      true,
    ]);
  });
});

describe('VoiceSettingsDialog for an agent without voice input of its own', () => {
  beforeEach(() => {
    client.setQueryData(
      queryKeys.agentCapabilities(AGENT.agentId),
      agentVersionSchema.parse({
        name: 'mobaide-agent',
        server_version: '1.2.0',
        protocol_versions: [7],
        min_client_version: 1,
        capabilities: {},
      }),
    );
  });

  it('disables "On this computer" and says why, but not for an agent already set to it', async () => {
    const reason = localBlocker(AGENT.name, false, undefined, false);
    if (reason === null) throw new Error('an agent without voice input must block the option');
    client.setQueryData(queryKeys.cloudVoice(), cloud({}));
    render(
      <QueryClientProvider client={client}>
        <VoiceSettingsDialog agent={AGENT} onClose={() => undefined} />
      </QueryClientProvider>,
    );

    const engines = screen.getByRole('radiogroup');
    expect(within(engines).getByRole('radio', { name: LOCAL_VOICE_NAME })).toBeDisabled();
    expect(within(engines).getByText(reason)).toBeInTheDocument();
    for (const engine of within(engines).getAllByRole('radio')) expect(engine).not.toBeChecked();

    // Already set to its own voice, here or on another device: the picked
    // option stays usable and is not marked as blocked.
    act(() => {
      client.setQueryData(queryKeys.cloudVoice(), cloud({ [AGENT.agentId]: 'local' }));
    });

    await waitFor(() => {
      expect(within(engines).getByRole('radio', { name: LOCAL_VOICE_NAME })).toBeChecked();
    });
    expect(within(engines).getByRole('radio', { name: LOCAL_VOICE_NAME })).toBeEnabled();
    expect(within(engines).queryByText(reason)).toBeNull();
  });
});

describe('VoiceSettingsDialog for an agent that did not answer', () => {
  const unreachable = localBlocker(AGENT.name, true, undefined, true);
  const checking = localBlocker(AGENT.name, true, undefined, false);
  if (unreachable === null || checking === null) throw new Error('both states must have a line');

  const offline = (): Promise<never> => Promise.reject(new Error('the agent is offline'));
  const noAnswer = (): Promise<never> => new Promise<never>(() => undefined);
  /** From here on the agent answers. */
  const comeBack = () => {
    agentApi.version = () => Promise.resolve(WITH_VOICE);
    agentApi.voiceStatus = () => Promise.resolve(NOT_INSTALLED);
  };

  const dialog = (agent: Agent) => (
    <QueryClientProvider client={client}>
      <VoiceSettingsDialog agent={agent} onClose={() => undefined} />
    </QueryClientProvider>
  );
  const advance = (ms: number) =>
    act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });

  beforeEach(() => {
    vi.useFakeTimers();
    client.removeQueries({ queryKey: queryKeys.agentCapabilities(AGENT.agentId) });
    client.removeQueries({ queryKey: queryKeys.voiceStatus(AGENT.agentId) });
    client.setQueryData(queryKeys.cloudVoice(), cloud({ [AGENT.agentId]: 'local' }));
    agentApi.probes = 0;
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('asks again by itself until the agent answers, then stops asking', async () => {
    agentApi.version = offline;
    render(dialog(AGENT));
    await advance(1);
    expect(screen.getByRole('alert')).toHaveTextContent(unreachable);

    let answer: ((version: AgentVersionInfo) => void) | undefined;
    agentApi.version = () =>
      new Promise<AgentVersionInfo>((resolve) => {
        answer = resolve;
      });
    await advance(VOICE_UNREACHABLE_POLL_MS);

    // The retry is on its way: still the failure, not a first check again.
    expect(agentApi.probes).toBe(2);
    expect(screen.getByRole('alert')).toHaveTextContent(unreachable);
    expect(screen.queryByText(checking)).toBeNull();

    comeBack();
    answer?.(WITH_VOICE);
    await advance(1);

    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByRole('button', { name: 'Enable voice input' })).toBeInTheDocument();
    await advance(VOICE_UNREACHABLE_POLL_MS * 3);
    expect(agentApi.probes).toBe(2);
  });

  it.each([
    ['failed', offline, unreachable],
    ['is still waiting for an answer', noAnswer, checking],
  ])(
    'asks again at once when the agent comes back online after a request that %s',
    async (_label, whileAway, line) => {
      agentApi.version = whileAway;
      const view = render(dialog({ ...AGENT, online: false }));
      await advance(1);
      expect(screen.getByText(line, { exact: false })).toBeInTheDocument();

      comeBack();
      view.rerender(dialog({ ...AGENT, online: true }));
      // Far less than the retry interval: the agent list's change did this.
      await advance(1);

      expect(screen.queryByText(line, { exact: false })).toBeNull();
      expect(screen.getByRole('button', { name: 'Enable voice input' })).toBeInTheDocument();
    },
  );
});

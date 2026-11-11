import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubLocalStorage } from '../test/localStorageStub';

// Before importing the store, so zustand's
// `createJSONStorage(() => window.localStorage)` captures the stand-in.
stubLocalStorage();

const setVoiceCleanup = vi.fn();
vi.mock('../api/voiceRepository', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/voiceRepository')>()),
  setVoiceCleanup: (agentId: string, enabled: boolean) => setVoiceCleanup(agentId, enabled) as unknown,
}));

vi.mock('../api/agentCapabilities', () => ({
  fetchAgentCapabilities: () => Promise.reject(new Error('the capability comes from the seeded cache')),
}));

const { queryKeys } = await import('../lib/queryKeys');
const { agentVersionSchema } = await import('../protocol/dto/agentVersion');
const { voiceStatusSchema } = await import('../protocol/dto/voice');
const { useVoicePrefsStore } = await import('./voicePrefsStore');
const { VoicePanel } = await import('./VoicePanel');

const AGENT = 'a1';
const VOICE = { dictation: true, sample_rate: 16000, max_seconds: 120, max_chunk_bytes: 65536 };

const READY_WIRE = {
  state: 'ready',
  enabled: true,
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
};
const READY = voiceStatusSchema.parse(READY_WIRE);

const status = (state: string) =>
  voiceStatusSchema.parse({
    ...READY_WIRE,
    state,
    enabled: state !== 'not_installed',
    progress: state === 'downloading' ? { done_bytes: 1, total_bytes: 2 } : null,
    error: state === 'failed' ? { code: 'download_failed', message: 'boom' } : null,
  });

const LABEL =
  'Clean up dictated text with the session’s AI agent: remove filler words, fix punctuation and technical terms';

let client: QueryClient;

function mount(
  variant: 'setup' | 'settings',
  voice: Record<string, unknown>,
  options: { status?: ReturnType<typeof status>; cleanupOffered?: boolean } = {},
): void {
  client.setQueryData(
    queryKeys.agentCapabilities(AGENT),
    agentVersionSchema.parse({
      name: 'mobaide-agent',
      server_version: '1.4.0',
      protocol_versions: [7],
      min_client_version: 1,
      capabilities: { voice },
    }),
  );
  render(
    <QueryClientProvider client={client}>
      <VoicePanel
        agentId={AGENT}
        agentName="laptop"
        variant={variant}
        status={options.status ?? READY}
        loadFailed={false}
        cleanupOffered={options.cleanupOffered}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  useVoicePrefsStore.setState({
    hiddenAgents: {},
    skipReviewAgents: {},
    cleanupNoticeSeen: false,
  });
  window.localStorage.clear();
  setVoiceCleanup.mockReset();
  setVoiceCleanup.mockImplementation((_agentId: string, enabled: boolean) =>
    Promise.resolve(voiceStatusSchema.parse({ ...READY_WIRE, cleanup_enabled: enabled })),
  );
});

afterEach(() => {
  cleanup();
});

describe('VoicePanel transcript cleanup', () => {
  it('offers the toggle in the settings, and stores it on the agent after the notice', async () => {
    mount('settings', { ...VOICE, cleanup: true });
    const toggle = screen.getByRole('checkbox', { name: LABEL });
    expect(toggle).not.toBeChecked();

    fireEvent.click(toggle);
    expect(setVoiceCleanup).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Got it' }));

    await waitFor(() => {
      expect(setVoiceCleanup).toHaveBeenCalledWith(AGENT, true);
    });
    expect(useVoicePrefsStore.getState().cleanupNoticeSeen).toBe(true);
  });

  it('shows the choice the agent stored, from any client', () => {
    mount('settings', { ...VOICE, cleanup: true }, {
      status: voiceStatusSchema.parse({ ...READY_WIRE, cleanup_enabled: true }),
    });

    expect(screen.getByRole('checkbox', { name: LABEL })).toBeChecked();
  });

  it('turns it off on the agent without a notice', async () => {
    mount('settings', { ...VOICE, cleanup: true }, {
      status: voiceStatusSchema.parse({ ...READY_WIRE, cleanup_enabled: true }),
    });

    fireEvent.click(screen.getByRole('checkbox', { name: LABEL }));

    await waitFor(() => {
      expect(setVoiceCleanup).toHaveBeenCalledWith(AGENT, false);
    });
    expect(screen.queryByRole('button', { name: 'Got it' })).toBeNull();
  });

  it('says so when the agent does not store the change', async () => {
    setVoiceCleanup.mockRejectedValue(new Error('offline'));
    useVoicePrefsStore.setState({ cleanupNoticeSeen: true });
    mount('settings', { ...VOICE, cleanup: true });

    fireEvent.click(screen.getByRole('checkbox', { name: LABEL }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Couldn’t save this setting on laptop.');
    expect(screen.getByRole('checkbox', { name: LABEL })).not.toBeChecked();
  });

  it.each(['queued', 'ready'])(
    'offers the toggle in the setup sheet of a session that can clean up, while %s',
    (state) => {
      mount('setup', VOICE, { status: status(state), cleanupOffered: true });

      expect(screen.getByRole('checkbox', { name: LABEL })).not.toBeChecked();
    },
  );

  it.each([
    ['settings of an agent without capabilities.voice.cleanup', 'settings', VOICE, false, 'ready'],
    ['setup sheet of a session that cannot clean up', 'setup', { ...VOICE, cleanup: true }, false, 'ready'],
    ['setup sheet before voice is installed', 'setup', VOICE, true, 'not_installed'],
  ] as const)('leaves the toggle out of the %s', (_label, variant, voice, cleanupOffered, state) => {
    mount(variant, voice, { status: status(state), cleanupOffered });

    expect(screen.queryByRole('checkbox', { name: /Clean up dictated text/ })).toBeNull();
  });
});

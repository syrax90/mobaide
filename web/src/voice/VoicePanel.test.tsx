import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubLocalStorage } from '../test/localStorageStub';

// Before importing the store, so zustand's
// `createJSONStorage(() => window.localStorage)` captures the stand-in.
stubLocalStorage();

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
    cleanupAgents: {},
    cleanupNoticeSeen: false,
  });
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
});

describe('VoicePanel transcript cleanup', () => {
  it('offers the toggle in the settings, and turns it on after the notice', () => {
    mount('settings', { ...VOICE, cleanup: true });
    const toggle = screen.getByRole('checkbox', { name: LABEL });
    expect(toggle).not.toBeChecked();

    fireEvent.click(toggle);
    expect(useVoicePrefsStore.getState().cleanupAgents).toEqual({});
    fireEvent.click(screen.getByRole('button', { name: 'Got it' }));

    expect(toggle).toBeChecked();
    expect(useVoicePrefsStore.getState().cleanupAgents).toEqual({ [AGENT]: true });
  });

  it('names no single AI agent and drops the old explanation under the toggle', () => {
    mount('settings', { ...VOICE, cleanup: true });

    expect(screen.queryByText(/Claude Haiku/)).toBeNull();
    expect(screen.queryByText(/Removes filler words/)).toBeNull();
    expect(screen.queryByText(/sent to Anthropic/)).toBeNull();
  });

  it.each(['queued', 'downloading', 'verifying', 'ready'])(
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
    ['setup sheet of a failed install', 'setup', VOICE, true, 'failed'],
  ] as const)('leaves the toggle out of the %s', (_label, variant, voice, cleanupOffered, state) => {
    mount(variant, voice, { status: status(state), cleanupOffered });

    expect(screen.queryByRole('checkbox', { name: /Clean up dictated text/ })).toBeNull();
  });
});

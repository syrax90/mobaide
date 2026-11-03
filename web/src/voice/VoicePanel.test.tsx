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

const READY = voiceStatusSchema.parse({
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
});

let client: QueryClient;

function mount(variant: 'setup' | 'settings', voice: Record<string, unknown>): void {
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
      <VoicePanel agentId={AGENT} agentName="laptop" variant={variant} status={READY} loadFailed={false} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  useVoicePrefsStore.setState({ hiddenAgents: {}, skipReviewAgents: {}, cleanupAgents: {} });
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
});

describe('VoicePanel transcript cleanup', () => {
  it('offers the toggle in the settings, described by where the text goes', () => {
    mount('settings', { ...VOICE, cleanup: true });
    const toggle = screen.getByRole('checkbox', {
      name: 'Clean up dictated text with Claude Haiku in Claude Code sessions',
    });

    expect(toggle).not.toBeChecked();
    expect(toggle).toHaveAccessibleDescription(
      'Removes filler words and false starts and fixes punctuation and technical terms before the ' +
        'text is inserted. Each transcript is sent to Anthropic through Claude Code on laptop and ' +
        'counts toward that Claude account’s usage.',
    );

    fireEvent.click(toggle);

    expect(toggle).toBeChecked();
    expect(useVoicePrefsStore.getState().cleanupAgents).toEqual({ [AGENT]: true });
  });

  it.each([
    ['settings of an agent without capabilities.voice.cleanup', 'settings', VOICE],
    ['setup sheet', 'setup', { ...VOICE, cleanup: true }],
  ] as const)('leaves the toggle out of the %s', (_label, variant, voice) => {
    mount(variant, voice);

    expect(screen.queryByRole('checkbox', { name: /Clean up dictated text/ })).toBeNull();
    expect(screen.queryByText(/sent to Anthropic/)).toBeNull();
  });
});

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { VoiceCapability } from '../protocol/dto/agentVersion';

// Hand-written stand-ins for what the hook reads: the agent's capability and
// voice status, and the account's cloud voice status. Each test sets them and
// re-renders; what is under test is how the hook combines them.
interface Sources {
  capability: { data: unknown; isPending: boolean };
  status: { data: unknown; isError: boolean };
  cloud: unknown;
  cleanupKinds: string[];
  cloudRefetches: number;
}
const sources = vi.hoisted(
  (): Sources => ({
    capability: { data: undefined, isPending: true },
    status: { data: undefined, isError: false },
    cloud: undefined,
    cleanupKinds: [],
    cloudRefetches: 0,
  }),
);

vi.mock('./useVoiceStatus', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./useVoiceStatus')>()),
  useVoiceCapability: () => sources.capability,
  useVoiceStatus: () => sources.status,
  useVoiceCleanupKinds: () => ({ data: sources.cleanupKinds }),
  useVoiceCleanupSetting: () => ({ on: true, set: () => undefined, failed: false }),
}));
vi.mock('./useCloudVoice', () => ({
  useCloudVoice: () => ({
    data: sources.cloud,
    isFetching: false,
    refetch: () => {
      sources.cloudRefetches += 1;
      return Promise.resolve();
    },
  }),
}));
vi.mock('../api/agentRepository', () => ({ listAgents: () => Promise.resolve([]) }));

const { cloudVoiceSchema } = await import('../protocol/dto/cloudVoice');
const { voiceStatusSchema } = await import('../protocol/dto/voice');
const { useComposerVoice } = await import('./useComposerVoice');

const AGENT = 'a1';
const CAPABILITY: VoiceCapability = { sampleRate: 16000, maxSeconds: 120, maxChunkBytes: 65536 };

const cloud = (state: string, engines: Record<string, string> = {}) =>
  cloudVoiceSchema.parse({
    state,
    limits: { sample_rate: 16000, max_seconds: 120, max_chunk_bytes: 65536 },
    usage: { used_seconds: 0, limit_seconds: 1800, resets_at: '2026-11-23T00:00:00Z' },
    engines,
  });

const localStatus = (state: string, enabled: boolean) =>
  voiceStatusSchema.parse({
    state,
    enabled,
    repairing: false,
    model: { id: 'm', name: 'M', languages: ['en'], download_bytes: 1, disk_bytes: 1, ram_bytes: 1 },
    progress: null,
    host: { recommendation: 'ok', reasons: [] },
    speed_factor: null,
    error: null,
  });

let client: QueryClient;
const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);
const insert = () => true;
const render = () =>
  renderHook(() => useComposerVoice(AGENT, 's1', { insert, sessionKind: 'claude' }), { wrapper });

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  sources.capability = { data: undefined, isPending: true };
  sources.status = { data: undefined, isError: false };
  sources.cloud = undefined;
  sources.cleanupKinds = ['claude'];
  sources.cloudRefetches = 0;
});

describe('useComposerVoice', () => {
  it('shows no mic until the agent\'s own voice is known, then asks or keeps it', () => {
    sources.cloud = cloud('ready');
    const hook = render();
    // Still loading: "no choice" may yet be an agent with voice enabled, and
    // a mic that opens the chooser must not flash for it.
    expect(hook.result.current).toMatchObject({ route: 'choose', micMode: 'hidden' });

    sources.capability = { data: CAPABILITY, isPending: false };
    hook.rerender();
    expect(hook.result.current).toMatchObject({ route: 'choose', micMode: 'hidden' });

    sources.status = { data: localStatus('not_installed', false), isError: false };
    hook.rerender();
    expect(hook.result.current).toMatchObject({ route: 'choose', micMode: 'off' });

    sources.status = { data: localStatus('ready', true), isError: false };
    hook.rerender();
    expect(hook.result.current).toMatchObject({
      route: 'local',
      micMode: 'ready',
      cleanupAvailable: true,
    });
  });

  it('gives an agent without voice of its own a mic when cloud is offered', () => {
    sources.capability = { data: null, isPending: false };
    sources.cloud = cloud('off');
    const hook = render();
    expect(hook.result.current).toMatchObject({ route: 'local', micMode: 'hidden' });

    sources.cloud = cloud('ready');
    hook.rerender();
    expect(hook.result.current).toMatchObject({
      route: 'choose',
      micMode: 'off',
      localSupported: false,
    });

    sources.cloud = cloud('ready', { [AGENT]: 'cloud' });
    hook.rerender();
    expect(hook.result.current).toMatchObject({ route: 'cloud', micMode: 'ready' });
  });

  it('offers no transcript cleanup on the cloud route', () => {
    sources.capability = { data: CAPABILITY, isPending: false };
    sources.status = { data: localStatus('ready', true), isError: false };
    sources.cloud = cloud('ready', { [AGENT]: 'cloud' });

    const hook = render();

    expect(hook.result.current).toMatchObject({
      route: 'cloud',
      micMode: 'ready',
      cleanupAvailable: false,
    });
  });

  it.each(['unavailable', 'limit_reached'])(
    'explains instead of recording while cloud is %s, and asks the server again',
    (state) => {
      sources.capability = { data: CAPABILITY, isPending: false };
      sources.status = { data: localStatus('ready', true), isError: false };
      sources.cloud = cloud(state, { [AGENT]: 'cloud' });
      const hook = render();
      expect(hook.result.current).toMatchObject({ route: 'cloud', micMode: 'unavailable' });

      act(() => {
        hook.result.current.onMicClick();
      });

      expect(hook.result.current.setupOpen).toBe(true);
      expect(hook.result.current.dictation.state.kind).toBe('idle');
      expect(sources.cloudRefetches).toBe(1);
    },
  );
});

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const setVoiceEngine = vi.fn();
vi.mock('../api/cloudVoiceRepository', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/cloudVoiceRepository')>()),
  setVoiceEngine: (agentId: string, engine: string) => setVoiceEngine(agentId, engine) as unknown,
}));

const { queryKeys } = await import('../lib/queryKeys');
const { cloudVoiceSchema } = await import('../protocol/dto/cloudVoice');
const { useVoiceEngineChoice } = await import('./useCloudVoice');

const AGENT = 'a1';
const status = (engines: Record<string, string>, usedSeconds = 0) =>
  cloudVoiceSchema.parse({
    state: 'ready',
    limits: { sample_rate: 16000, max_seconds: 120, max_chunk_bytes: 65536 },
    usage: { used_seconds: usedSeconds, limit_seconds: 1800, resets_at: '2026-11-23T00:00:00Z' },
    engines,
  });

let client: QueryClient;
const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);
const cached = () => client.getQueryData<ReturnType<typeof status>>(queryKeys.cloudVoice());

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  setVoiceEngine.mockReset();
});

describe('useVoiceEngineChoice', () => {
  it('shows the choice at once and restores the old one when the server refuses', async () => {
    let refuse!: (error: Error) => void;
    setVoiceEngine.mockReturnValue(new Promise((_resolve, reject) => (refuse = reject)));
    client.setQueryData(queryKeys.cloudVoice(), status({ other: 'local' }));
    const { result } = renderHook(() => useVoiceEngineChoice(AGENT), { wrapper });

    act(() => {
      result.current.mutate('cloud');
    });
    await waitFor(() => {
      expect(cached()?.engines).toEqual({ other: 'local', [AGENT]: 'cloud' });
    });
    act(() => {
      refuse(new Error('offline'));
    });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
    expect(cached()).toEqual(status({ other: 'local' }));
    expect(setVoiceEngine).toHaveBeenCalledWith(AGENT, 'cloud');
  });

  it('replaces the cache with the status the server answers', async () => {
    const answer = status({ [AGENT]: 'local' }, 61);
    setVoiceEngine.mockResolvedValue(answer);
    client.setQueryData(queryKeys.cloudVoice(), status({ [AGENT]: 'cloud' }));
    const { result } = renderHook(() => useVoiceEngineChoice(AGENT), { wrapper });

    act(() => {
      result.current.mutate('local');
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });
    expect(cached()).toEqual(answer);
  });
});

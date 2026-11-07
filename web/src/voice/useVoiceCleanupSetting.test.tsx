import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const setVoiceCleanup = vi.fn();
vi.mock('../api/voiceRepository', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/voiceRepository')>()),
  setVoiceCleanup: (agentId: string, enabled: boolean) => setVoiceCleanup(agentId, enabled) as unknown,
}));

const { queryKeys } = await import('../lib/queryKeys');
const { voiceStatusSchema } = await import('../protocol/dto/voice');
const { useVoiceCleanupSetting } = await import('./useVoiceStatus');

const AGENT = 'a1';
const status = (state: string, cleanup = false) =>
  voiceStatusSchema.parse({
    state,
    enabled: true,
    repairing: false,
    model: { id: 'm', name: 'M', languages: ['en'], download_bytes: 1, disk_bytes: 1, ram_bytes: 1 },
    progress: null,
    host: { recommendation: 'ok', reasons: [] },
    speed_factor: null,
    error: null,
    cleanup_enabled: cleanup,
  });

let client: QueryClient;
const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);
const cached = () => client.getQueryData<ReturnType<typeof status>>(queryKeys.voiceStatus(AGENT));

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  setVoiceCleanup.mockReset();
});

describe('useVoiceCleanupSetting', () => {
  it('shows the change at once and restores the old value when the agent refuses', async () => {
    let refuse!: (error: Error) => void;
    setVoiceCleanup.mockReturnValue(new Promise((_resolve, reject) => (refuse = reject)));
    client.setQueryData(queryKeys.voiceStatus(AGENT), status('ready'));
    const { result } = renderHook(() => useVoiceCleanupSetting(AGENT, cached()), { wrapper });

    act(() => {
      result.current.set(true);
    });
    await waitFor(() => {
      expect(cached()?.cleanup_enabled).toBe(true);
    });
    act(() => {
      refuse(new Error('offline'));
    });

    await waitFor(() => {
      expect(result.current.failed).toBe(true);
    });
    expect(cached()).toEqual(status('ready'));
  });

  it('keeps a voice.changed snapshot that arrived while the request was pending', async () => {
    let refuse!: (error: Error) => void;
    setVoiceCleanup.mockReturnValue(new Promise((_resolve, reject) => (refuse = reject)));
    client.setQueryData(queryKeys.voiceStatus(AGENT), status('downloading'));
    const { result } = renderHook(() => useVoiceCleanupSetting(AGENT, cached()), { wrapper });

    act(() => {
      result.current.set(true);
    });
    await waitFor(() => {
      expect(cached()?.cleanup_enabled).toBe(true);
    });
    client.setQueryData(queryKeys.voiceStatus(AGENT), status('ready'));
    act(() => {
      refuse(new Error('offline'));
    });

    await waitFor(() => {
      expect(result.current.failed).toBe(true);
    });
    expect(cached()).toEqual(status('ready'));
  });
});

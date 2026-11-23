import { QueryClient, QueryClientProvider, type QueryKey } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { FakeRecorder, FakeVoiceApi, ramp, refusal } from './__testing__/fakeVoiceApi';

// The two places a dictation can be uploaded to, and the microphone, are the
// scripted doubles of the controller tests.
interface Doubles {
  cloudApi: unknown;
  localApi: unknown;
  recorder: unknown;
}
const doubles = vi.hoisted((): Doubles => ({ cloudApi: null, localApi: null, recorder: null }));

vi.mock('../api/cloudVoiceRepository', () => ({
  createCloudVoiceApi: () => doubles.cloudApi,
}));
vi.mock('../api/voiceRepository', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/voiceRepository')>()),
  createVoiceApi: () => doubles.localApi,
}));
vi.mock('./recorder', () => ({
  WebAudioRecorder: class {
    constructor() {
      return doubles.recorder as object;
    }
  },
}));

const { useDictation } = await import('./useDictation');
type DictationSource = import('./useDictation').DictationSource;

const CLOUD: DictationSource = { engine: 'cloud', sampleRate: 16_000, maxSeconds: 120 };
const LOCAL: DictationSource = { engine: 'local', sampleRate: 16_000, maxSeconds: 120 };

let client: QueryClient;
let cloudApi: FakeVoiceApi;
let localApi: FakeVoiceApi;
let recorder: FakeRecorder;
let invalidated: QueryKey[];

const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  invalidated = [];
  vi.spyOn(client, 'invalidateQueries').mockImplementation((filters) => {
    invalidated.push(filters?.queryKey ?? []);
    return Promise.resolve();
  });
  cloudApi = new FakeVoiceApi();
  cloudApi.maxChunkBytes = 65_536;
  localApi = new FakeVoiceApi();
  localApi.maxChunkBytes = 65_536;
  recorder = new FakeRecorder();
  doubles.cloudApi = cloudApi;
  doubles.localApi = localApi;
  doubles.recorder = recorder;
});

function dictate(source: DictationSource | null) {
  return renderHook(({ current }) => useDictation('a1', 's1', current), {
    wrapper,
    initialProps: { current: source },
  });
}

/** Record one second and stop; the upload's `finish` answers as scripted. */
async function recordAndStop(hook: ReturnType<typeof dictate>): Promise<void> {
  act(() => {
    hook.result.current.toggle();
  });
  await waitFor(() => {
    expect(hook.result.current.state.kind).toBe('recording');
  });
  act(() => {
    recorder.sink?.samples(ramp(16_000));
    hook.result.current.stop();
  });
}

describe('useDictation', () => {
  it('keeps a failed cloud recording for Retry when the status turns unavailable', async () => {
    cloudApi.finishReplies.push({ fail: refusal(503, 'cloud_unavailable') });
    const hook = dictate(CLOUD);

    await recordAndStop(hook);
    await waitFor(() => {
      expect(hook.result.current.state).toMatchObject({
        kind: 'failed',
        code: 'cloud_unavailable',
        retryable: true,
        hasAudio: true,
      });
    });
    // The failure refreshes the status, which now says "unavailable": the
    // screen has no source to offer any more.
    expect(invalidated).toContainEqual(['cloud-voice']);
    hook.rerender({ current: null });

    expect(hook.result.current.state.kind).toBe('failed');
    expect(cloudApi.log).not.toContain('remove d1');

    act(() => {
      hook.result.current.retry();
    });
    await waitFor(() => {
      expect(hook.result.current.state).toEqual({ kind: 'result', text: 'hello world' });
    });
    expect(localApi.log).toEqual([]);
  });

  it('lets go of the cloud source once the dictation is over', async () => {
    cloudApi.finishReplies.push({ fail: refusal(503, 'cloud_unavailable') });
    const hook = dictate(CLOUD);
    await recordAndStop(hook);
    await waitFor(() => {
      expect(hook.result.current.state.kind).toBe('failed');
    });
    hook.rerender({ current: null });

    act(() => {
      hook.result.current.dismiss();
    });
    await waitFor(() => {
      expect(hook.result.current.state.kind).toBe('idle');
    });
    const creates = cloudApi.log.filter((entry) => entry === 'create').length;
    act(() => {
      hook.result.current.toggle();
    });

    // Nothing can record now: the mic tap starts no new dictation anywhere.
    expect(hook.result.current.state.kind).toBe('idle');
    expect(cloudApi.log.filter((entry) => entry === 'create')).toHaveLength(creates);
    expect(localApi.log).toEqual([]);
  });

  it('refreshes cloud voice after a cloud transcript, for the day\'s use', async () => {
    const hook = dictate(CLOUD);

    await recordAndStop(hook);

    await waitFor(() => {
      expect(hook.result.current.state).toEqual({ kind: 'result', text: 'hello world' });
    });
    expect(invalidated).toEqual([['cloud-voice']]);
  });

  it('drops an on-computer dictation when its source goes, and refreshes nothing for cloud', async () => {
    localApi.finishReplies.push({ fail: refusal(503, 'transcription_failed') });
    const hook = dictate(LOCAL);

    await recordAndStop(hook);
    await waitFor(() => {
      expect(hook.result.current.state.kind).toBe('failed');
    });
    hook.rerender({ current: null });

    await waitFor(() => {
      expect(hook.result.current.state.kind).toBe('idle');
    });
    expect(localApi.log).toContain('remove d1');
    expect(cloudApi.log).toEqual([]);
    expect(invalidated).not.toContainEqual(['cloud-voice']);
  });

  it('keeps the reviewed text when "dictate more" fails after its source went away', async () => {
    const hook = dictate(CLOUD);
    await recordAndStop(hook);
    await waitFor(() => {
      expect(hook.result.current.state).toEqual({ kind: 'result', text: 'hello world' });
    });
    cloudApi.finishReplies.push({ fail: refusal(503, 'cloud_unavailable') });
    const carried = { before: 'hello world', after: '' };
    act(() => {
      hook.result.current.continueFrom(carried);
    });
    await waitFor(() => {
      expect(hook.result.current.state.kind).toBe('recording');
    });
    act(() => {
      recorder.sink?.samples(ramp(16_000));
      hook.result.current.stop();
    });
    await waitFor(() => {
      expect(hook.result.current.state.kind).toBe('failed');
    });
    // The failure turns the status to "unavailable": no source is offered.
    hook.rerender({ current: null });

    act(() => {
      hook.result.current.dismiss();
    });
    await waitFor(() => {
      expect(hook.result.current.state.kind).toBe('idle');
    });

    // Back to reviewing: a released source would dispose the controller and
    // drop the text.
    expect(hook.result.current.carried).toEqual(carried);
  });
});


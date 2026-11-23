import { afterEach, describe, expect, it, vi } from 'vitest';

// Hand-written stand-in for the typed relay client, as in
// `voiceRepository.test.ts`: what is under test is the repository's own
// request shape, the "not offered" rule and the error mapping.
const relay = vi.hoisted(() => ({
  results: [] as { data?: unknown; error?: unknown; response: Response }[],
  requests: [] as { method: string; path: string; init: unknown }[],
}));

vi.mock('./clients', () => {
  const call = (method: string) => (path: string, init: unknown) => {
    relay.requests.push({ method, path, init });
    const next = relay.results.shift();
    if (next === undefined) return Promise.reject(new TypeError('Failed to fetch'));
    return Promise.resolve(next);
  };
  return {
    relayClient: { GET: call('GET'), PUT: call('PUT'), POST: call('POST'), DELETE: call('DELETE') },
    createAgentClient: () => {
      throw new Error('cloud voice input must not call an agent');
    },
  };
});

const { createCloudVoiceApi, getCloudVoice, setVoiceEngine } = await import('./cloudVoiceRepository');

afterEach(() => {
  relay.results.length = 0;
  relay.requests.length = 0;
});

const STATUS = {
  state: 'ready',
  limits: { sample_rate: 16000, max_seconds: 120, max_chunk_bytes: 65536 },
  usage: { used_seconds: 742, limit_seconds: 1800, resets_at: '2026-11-23T00:00:00Z' },
  engines: { a1b2c3d4e5f6: 'cloud' },
};

const answers = (status: number, body: unknown = undefined) => {
  const response = new Response(null, { status });
  relay.results.push(status < 400 ? { data: body, response } : { error: body, response });
};

describe('getCloudVoice', () => {
  it('reads a server without the route as cloud voice not offered', async () => {
    answers(404, { detail: 'Not Found' });

    const status = await getCloudVoice();

    expect(status.state).toBe('off');
    expect(status.engines).toEqual({});
    expect(relay.requests).toEqual([{ method: 'GET', path: '/api/voice/cloud', init: {} }]);
  });

  it('raises any other failure instead of reading it as not offered', async () => {
    answers(500, { detail: 'Internal Server Error' });

    await expect(getCloudVoice()).rejects.toMatchObject({ status: 500 });
  });

  it('keeps a state and an engine from a newer server as they are', async () => {
    answers(200, { ...STATUS, state: 'paused', engines: { a1b2c3d4e5f6: 'remote' } });

    await expect(getCloudVoice()).resolves.toMatchObject({
      state: 'paused',
      engines: { a1b2c3d4e5f6: 'remote' },
    });
  });
});

describe('setVoiceEngine', () => {
  it('stores the choice for the agent and returns the status the server answers', async () => {
    answers(200, STATUS);

    const status = await setVoiceEngine('a1b2c3d4e5f6', 'cloud');

    expect(status).toEqual(STATUS);
    expect(relay.requests).toEqual([
      {
        method: 'PUT',
        path: '/api/voice/cloud/engines/{agent_id}',
        init: { params: { path: { agent_id: 'a1b2c3d4e5f6' } }, body: { engine: 'cloud' } },
      },
    ]);
  });

  it('raises the coded refusal when cloud is not offered to the account', async () => {
    answers(403, { detail: { code: 'cloud_disabled', message: 'Cloud voice input is not available.' } });

    await expect(setVoiceEngine('a1b2c3d4e5f6', 'cloud')).rejects.toMatchObject({
      status: 403,
      code: 'cloud_disabled',
      network: false,
    });
  });
});

describe('createCloudVoiceApi', () => {
  const signal = new AbortController().signal;

  it('uploads one dictation through the central server routes', async () => {
    answers(200, { dictation_id: 'd1', sample_rate: 16000, max_seconds: 120, max_chunk_bytes: 65536 });
    answers(200, { seq: 0, duration_ms: 1000 });
    answers(200, { dictation_id: 'd1', state: 'transcribing', text: null, duration_ms: 1000 });
    answers(200, { dictation_id: 'd1', state: 'done', text: 'hello world', duration_ms: 1000 });
    const api = createCloudVoiceApi();

    const opened = await api.create(signal);
    await api.audio(opened.dictationId, 0, 'AAA=', signal);
    const waiting = await api.finish(opened.dictationId, 0, signal);
    const done = await api.finish(opened.dictationId, 0, signal);

    expect(opened).toEqual({ dictationId: 'd1', sampleRate: 16000, maxSeconds: 120, maxChunkBytes: 65536 });
    expect(waiting).toEqual({ state: 'transcribing', text: null });
    expect(done).toEqual({ state: 'done', text: 'hello world' });
    expect(relay.requests).toMatchObject([
      { method: 'POST', path: '/api/voice/cloud/dictations' },
      {
        method: 'POST',
        path: '/api/voice/cloud/dictations/{dictation_id}/audio',
        init: { params: { path: { dictation_id: 'd1' } }, body: { seq: 0, pcm16_b64: 'AAA=' } },
      },
      {
        method: 'POST',
        path: '/api/voice/cloud/dictations/{dictation_id}/finish',
        init: { params: { path: { dictation_id: 'd1' } }, body: { last_seq: 0 } },
      },
      { method: 'POST', path: '/api/voice/cloud/dictations/{dictation_id}/finish' },
    ]);
  });

  it.each([
    [503, 'cloud_unavailable'],
    [429, 'cloud_limit_reached'],
    [403, 'cloud_disabled'],
    [503, 'voice_busy'],
  ])('raises a %i %s refusal with its code', async (status, code) => {
    answers(status, { detail: { code, message: 'refused' } });

    await expect(createCloudVoiceApi().create(signal)).rejects.toMatchObject({ status, code });
  });

  it('marks a request that never got an answer as network', async () => {
    await expect(createCloudVoiceApi().finish('d1', 0, signal)).rejects.toMatchObject({
      status: null,
      network: true,
    });
  });

  it.each([204, 404])('treats a bodyless %i to DELETE as done', async (status) => {
    relay.results.push({ response: new Response(null, { status }) });

    await expect(createCloudVoiceApi().remove('d1')).resolves.toBeUndefined();
    expect(relay.requests[0]).toMatchObject({
      method: 'DELETE',
      path: '/api/voice/cloud/dictations/{dictation_id}',
      init: { params: { path: { dictation_id: 'd1' } } },
    });
  });

  it('reports any other DELETE failure', async () => {
    relay.results.push({ response: new Response(null, { status: 500 }) });

    await expect(createCloudVoiceApi().remove('d1')).rejects.toMatchObject({ status: 500 });
  });
});

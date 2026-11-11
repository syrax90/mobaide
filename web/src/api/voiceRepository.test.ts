import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ApiError } from './errors';

// Hand-written stand-in for the typed agent client, as in
// `sessionGitRepository.test.ts`: what is under test is the repository's own
// request shape and error mapping.
const agent = vi.hoisted(() => ({
  results: [] as { data?: unknown; error?: unknown; response: Response }[],
  requests: [] as { agentId: string; method: string; path: string; init: unknown }[],
}));

vi.mock('./clients', () => ({
  createAgentClient: (agentId: string) => {
    const call = (method: string) => (path: string, init: unknown) => {
      agent.requests.push({ agentId, method, path, init });
      const next = agent.results.shift();
      if (next === undefined) return Promise.reject(new TypeError('Failed to fetch'));
      return Promise.resolve(next);
    };
    return { GET: call('GET'), POST: call('POST'), DELETE: call('DELETE') };
  },
}));

const { cleanupTranscript, createVoiceApi, toVoiceApiError } = await import('./voiceRepository');
const { toApiError } = await import('./errors');

afterEach(() => {
  agent.results.length = 0;
  agent.requests.length = 0;
});

const apiError = (status: number, body: unknown): ApiError =>
  toApiError(new Response(null, { status }), body);

describe('toVoiceApiError', () => {
  it('lifts the code, message and expected seq out of the detail object', () => {
    const error = toVoiceApiError(
      apiError(409, {
        detail: { code: 'seq_gap', message: 'An earlier audio chunk is missing.', expected_seq: 3 },
      }),
    );

    expect(error).toMatchObject({
      status: 409,
      code: 'seq_gap',
      detailMessage: 'An earlier audio chunk is missing.',
      expectedSeq: 3,
      network: false,
    });
  });

  it('keeps a refusal without a coded detail as an uncoded status', () => {
    expect(toVoiceApiError(apiError(502, { detail: 'Bad gateway' }))).toMatchObject({
      status: 502,
      code: null,
      expectedSeq: null,
      network: false,
    });
  });

  it('marks a request that never got an answer as network', () => {
    expect(toVoiceApiError(new TypeError('Failed to fetch'))).toMatchObject({
      status: null,
      code: null,
      network: true,
    });
  });
});

describe('createVoiceApi', () => {
  it('opens a dictation for the session and reads its limits', async () => {
    agent.results.push({
      data: { dictation_id: 'd1', sample_rate: 16000, max_seconds: 120, max_chunk_bytes: 65536 },
      response: new Response(null, { status: 200 }),
    });

    const opened = await createVoiceApi('a1', 's1').create(new AbortController().signal);

    expect(opened).toEqual({ dictationId: 'd1', sampleRate: 16000, maxSeconds: 120, maxChunkBytes: 65536 });
    expect(agent.requests[0]).toMatchObject({
      agentId: 'a1',
      method: 'POST',
      path: '/api/voice/dictations',
      init: { body: { session_id: 's1' } },
    });
  });

  it('raises the coded refusal of an audio chunk', async () => {
    agent.results.push({
      error: { detail: { code: 'transcription_failed', message: 'model will not load' } },
      response: new Response(null, { status: 503 }),
    });

    const sending = createVoiceApi('a1', null).audio('d1', 0, 'AAA=', new AbortController().signal);

    await expect(sending).rejects.toMatchObject({ status: 503, code: 'transcription_failed' });
  });

  it.each([204, 404])('treats a bodyless %i to DELETE as done', async (status) => {
    agent.results.push({ response: new Response(null, { status }) });

    await expect(createVoiceApi('a1', null).remove('d1')).resolves.toBeUndefined();
    expect(agent.requests[0]).toMatchObject({
      method: 'DELETE',
      path: '/api/voice/dictations/{dictation_id}',
      init: { params: { path: { dictation_id: 'd1' } } },
    });
  });

  it('reports any other DELETE failure', async () => {
    agent.results.push({ response: new Response(null, { status: 500 }) });

    await expect(createVoiceApi('a1', null).remove('d1')).rejects.toMatchObject({ status: 500 });
  });
});

describe('cleanupTranscript', () => {
  it('posts the transcript and the session kind with the signal given and reads back the cleaned text', async () => {
    agent.results.push({
      data: { text: 'Fix the login bug.' },
      response: new Response(null, { status: 200 }),
    });
    const signal = new AbortController().signal;

    const cleaned = await cleanupTranscript('a1', 'um fix the uh login bug', 'codex', signal);

    expect(cleaned).toBe('Fix the login bug.');
    expect(agent.requests[0]).toMatchObject({
      agentId: 'a1',
      method: 'POST',
      path: '/api/voice/cleanup',
      init: { body: { text: 'um fix the uh login bug', kind: 'codex' }, signal },
    });
  });
});

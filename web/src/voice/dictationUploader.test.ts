import { beforeEach, describe, expect, it } from 'vitest';
import {
  FakeClock,
  FakeVoiceApi,
  Held,
  RecordingListener,
  networkError,
  ramp,
  refusal,
  settle,
} from './__testing__/fakeVoiceApi';
import { DictationUploader } from './dictationUploader';
import { PcmBuffer } from './pcmBuffer';
import type { VoiceDictationOpened, VoiceFinishResult } from './voiceApi';

// The fake agent answers `max_chunk_bytes: 8`, so a chunk is 4 samples.
const RATE = 16_000;

let api: FakeVoiceApi;
let clock: FakeClock;
let listener: RecordingListener;
let buffer: PcmBuffer;

beforeEach(() => {
  api = new FakeVoiceApi();
  clock = new FakeClock();
  listener = new RecordingListener();
  buffer = new PcmBuffer(1_000);
});

function uploader(): DictationUploader {
  return new DictationUploader(api, buffer, RATE, listener, clock);
}

/** Start an uploader over `samples` already recorded, then stop recording. */
async function uploadClosed(samples: number): Promise<DictationUploader> {
  buffer.append(ramp(samples));
  const upload = uploader();
  upload.start();
  upload.close();
  await settle();
  return upload;
}

describe('DictationUploader', () => {
  it('uploads full chunks while recording, then the tail, then finishes with the last seq', async () => {
    buffer.append(ramp(10));
    const upload = uploader();
    upload.start();
    await settle();

    expect(api.log).toEqual(['create', 'audio d1#0', 'audio d1#1']);

    upload.close();
    await settle();

    expect(api.log).toEqual(['create', 'audio d1#0', 'audio d1#1', 'audio d1#2', 'finish d1@2']);
    expect(api.payloads.get('d1#2')).toBe(buffer.payload(2, 4));
    expect(listener.events).toEqual(['done hello world']);
  });

  it('keeps exactly one audio request in flight and finishes only after the last is answered', async () => {
    const first = new Held<undefined>();
    api.audioReplies.push({ hold: first });
    buffer.append(ramp(4));
    const upload = uploader();
    upload.start();
    await settle();

    buffer.append(ramp(6, 100));
    upload.onAudio();
    upload.onAudio();
    upload.close();
    await settle();

    expect(api.log).toEqual(['create', 'audio d1#0']);

    first.resolve(undefined);
    await settle();

    expect(api.log).toEqual(['create', 'audio d1#0', 'audio d1#1', 'audio d1#2', 'finish d1@2']);
    expect(listener.events).toEqual(['done hello world']);
  });

  it('trims the transcript and reports an empty one as empty', async () => {
    api.text = '   ';

    await uploadClosed(3);

    expect(listener.events).toEqual(['done ']);
  });

  it('retries a lost request with 0.5/1/2/4 s backoff, ±20 % jitter, and reports lagging', async () => {
    clock.randomValue = 1;
    for (let i = 0; i < 5; i += 1) api.audioReplies.push({ fail: networkError() });

    await uploadClosed(4);

    expect(clock.sleeps).toEqual([600, 1_200, 2_400, 4_800, 4_800]);
    expect(api.log.filter((entry) => entry === 'audio d1#0')).toHaveLength(6);
    expect(listener.events).toEqual(['lagging true', 'lagging false', 'done hello world']);
  });

  it('retries an uncoded 5xx and a 503 voice_busy like a lost request', async () => {
    api.audioReplies.push({ fail: refusal(502, null) }, { fail: refusal(503, 'voice_busy') });

    await uploadClosed(4);

    expect(clock.sleeps).toEqual([500, 1_000]);
    expect(listener.events.at(-1)).toBe('done hello world');
  });

  it('never gives up on a stalled upload while still recording', async () => {
    for (let i = 0; i < 30; i += 1) api.audioReplies.push({ fail: networkError() });
    buffer.append(ramp(4));
    const upload = uploader();
    upload.start();
    await settle();

    expect(clock.nowMs).toBeGreaterThan(60_000);
    expect(listener.events).toEqual(['lagging true', 'lagging false']);
    expect(api.log.at(-1)).toBe('audio d1#0');
    upload.cancel();
  });

  it('fails a stalled upload 30 s after recording stopped, keeping it retryable', async () => {
    api.audioFallback = () => ({ fail: networkError() });

    await uploadClosed(4);

    expect(listener.failure).toEqual({ code: 'network', retryable: true, message: 'offline' });
    expect(clock.nowMs).toBeGreaterThan(30_000);
    expect(clock.nowMs).toBeLessThanOrEqual(30_000 + 4_000);
  });

  it('continues a live dictation where it stopped after a retry', async () => {
    api.audioReplies.push({ ok: undefined });
    api.audioFallback = () => ({ fail: networkError() });
    const upload = await uploadClosed(8);
    expect(listener.failure?.code).toBe('network');
    api.audioFallback = () => ({ ok: undefined });

    expect(upload.retry()).toBe(true);
    await settle();

    expect(api.log.filter((entry) => !entry.startsWith('audio d1#1'))).toEqual([
      'create',
      'audio d1#0',
      'finish d1@1',
    ]);
    expect(listener.events.at(-1)).toBe('done hello world');
  });

  it('resends from the expected seq after a seq_gap', async () => {
    api.audioReplies.push(
      { ok: undefined },
      { ok: undefined },
      { fail: refusal(409, 'seq_gap', { expectedSeq: 1 }) },
    );

    await uploadClosed(12);

    expect(api.log).toEqual([
      'create',
      'audio d1#0',
      'audio d1#1',
      'audio d1#2',
      'audio d1#1',
      'audio d1#2',
      'finish d1@2',
    ]);
    expect(listener.events).toEqual(['done hello world']);
  });

  it('resends from the expected seq when finish reports a gap', async () => {
    api.finishReplies.push({ fail: refusal(409, 'seq_gap', { expectedSeq: 1 }) });

    await uploadClosed(8);

    expect(api.log).toEqual([
      'create',
      'audio d1#0',
      'audio d1#1',
      'finish d1@1',
      'audio d1#1',
      'finish d1@1',
    ]);
    expect(listener.events).toEqual(['done hello world']);
  });

  it('gives up after three resyncs and when the agent expects a seq never sent', async () => {
    api.audioFallback = () => ({ fail: refusal(409, 'seq_gap', { expectedSeq: 0 }) });

    await uploadClosed(4);

    expect(api.log.filter((entry) => entry === 'audio d1#0')).toHaveLength(4);
    expect(listener.failure).toEqual({ code: 'seq_gap', retryable: false, message: null });

    api = new FakeVoiceApi();
    listener = new RecordingListener();
    buffer = new PcmBuffer(1_000);
    api.audioReplies.push({ fail: refusal(409, 'seq_gap', { expectedSeq: 5 }) });

    await uploadClosed(4);

    expect(listener.failure).toEqual({ code: 'seq_gap', retryable: false, message: null });
  });

  it('deletes an expired dictation, opens a new one and re-uploads from seq 0', async () => {
    api.audioReplies.push({ ok: undefined }, { fail: refusal(404, 'dictation_not_found') });

    await uploadClosed(8);

    expect(api.log).toEqual([
      'create',
      'audio d1#0',
      'audio d1#1',
      'remove d1',
      'create',
      'audio d2#0',
      'audio d2#1',
      'finish d2@1',
    ]);
    expect(api.payloads.get('d2#0')).toBe(api.payloads.get('d1#0'));
    expect(listener.events).toEqual(['done hello world']);
  });

  it('stops restarting after two and fails retryably', async () => {
    api.audioFallback = () => ({ fail: refusal(404, 'dictation_not_found') });

    await uploadClosed(4);

    expect(api.log.filter((entry) => entry === 'create')).toHaveLength(3);
    expect(api.log.filter((entry) => entry.startsWith('remove'))).toEqual(['remove d1', 'remove d2']);
    expect(listener.failure).toEqual({ code: 'dictation_not_found', retryable: true, message: null });
  });

  it('stops capture on too_long and finishes with the last accepted chunk', async () => {
    api.audioReplies.push({ ok: undefined }, { ok: undefined }, { fail: refusal(409, 'too_long') });
    buffer.append(ramp(12));
    const upload = uploader();
    upload.start();
    await settle();

    expect(listener.events).toEqual(['stopCapture', 'done hello world']);
    expect(api.log).toEqual(['create', 'audio d1#0', 'audio d1#1', 'audio d1#2', 'finish d1@1']);
    upload.close();
  });

  it('re-uploads from seq 0 when the dictation expires after too_long', async () => {
    const tooLong = { fail: refusal(409, 'too_long') };
    api.audioReplies.push({ ok: undefined }, { ok: undefined }, tooLong);
    api.audioReplies.push({ ok: undefined }, { ok: undefined }, tooLong);
    api.finishReplies.push({ fail: refusal(404, 'dictation_not_found') });
    buffer.append(ramp(12));
    const upload = uploader();
    upload.start();
    await settle();

    expect(api.log).toEqual([
      'create',
      'audio d1#0',
      'audio d1#1',
      'audio d1#2',
      'finish d1@1',
      'remove d1',
      'create',
      'audio d2#0',
      'audio d2#1',
      'audio d2#2',
      'finish d2@1',
    ]);
    expect(listener.events.at(-1)).toBe('done hello world');
    upload.close();
  });

  it('re-uploads from seq 0 when Retry opens a new dictation after too_long', async () => {
    api.audioReplies.push({ ok: undefined }, { ok: undefined }, { fail: refusal(409, 'too_long') });
    api.finishReplies.push({ fail: refusal(503, 'transcription_failed') });
    buffer.append(ramp(12));
    const upload = uploader();
    upload.start();
    await settle();
    expect(listener.failure?.code).toBe('transcription_failed');

    expect(upload.retry()).toBe(true);
    await settle();

    expect(api.log.slice(5)).toEqual([
      'remove d1',
      'create',
      'audio d2#0',
      'audio d2#1',
      'audio d2#2',
      'finish d2@2',
    ]);
    expect(listener.events.at(-1)).toBe('done hello world');
  });

  it('latches transcription_failed: no automatic re-upload, and Retry opens a new dictation', async () => {
    api.audioReplies.push(
      { ok: undefined },
      { fail: refusal(503, 'transcription_failed', { detailMessage: 'model will not load' }) },
    );
    const upload = await uploadClosed(8);

    expect(listener.failure).toEqual({
      code: 'transcription_failed',
      retryable: true,
      message: 'model will not load',
    });
    expect(api.log).toEqual(['create', 'audio d1#0', 'audio d1#1']);

    expect(upload.retry()).toBe(true);
    await settle();

    expect(api.log.slice(3)).toEqual([
      'remove d1',
      'create',
      'audio d2#0',
      'audio d2#1',
      'finish d2@1',
    ]);
    expect(listener.events.at(-1)).toBe('done hello world');
  });

  it('asks finish again while transcribing, at least 1 s apart', async () => {
    const transcribing: VoiceFinishResult = { state: 'transcribing', text: null };
    api.finishReplies.push({ ok: transcribing }, { ok: transcribing });

    await uploadClosed(4);

    expect(api.log.filter((entry) => entry.startsWith('finish'))).toHaveLength(3);
    expect(clock.sleeps).toEqual([1_000, 1_000]);
    expect(listener.events).toEqual(['done hello world']);
  });

  it('gives up on transcribing after the 120 s deadline, retryably', async () => {
    api.finishFallback = () => ({ ok: { state: 'transcribing', text: null } });

    await uploadClosed(4);

    expect(listener.failure).toEqual({
      code: 'transcription_timeout',
      retryable: true,
      message: null,
    });
    expect(clock.nowMs).toBeGreaterThan(120_000);
    expect(clock.nowMs).toBeLessThanOrEqual(122_000);
  });

  it('fails a finish that keeps getting lost 30 s after recording stopped, not at the deadline', async () => {
    api.finishFallback = () => ({ fail: networkError() });

    await uploadClosed(4);

    expect(listener.failure).toEqual({ code: 'network', retryable: true, message: 'offline' });
    expect(clock.nowMs).toBeGreaterThan(30_000);
    expect(clock.nowMs).toBeLessThanOrEqual(30_000 + 4_000);
  });

  it('restarts the 30 s finish stall window after any answer', async () => {
    const transcribing: VoiceFinishResult = { state: 'transcribing', text: null };
    api.finishReplies.push({ fail: networkError() });
    for (let i = 0; i < 31; i += 1) api.finishReplies.push({ ok: transcribing });
    api.finishReplies.push({ fail: networkError() });

    await uploadClosed(4);

    expect(clock.nowMs).toBeGreaterThan(30_000);
    expect(listener.events.at(-1)).toBe('done hello world');
  });

  it.each([
    [409, 'voice_busy', true],
    [409, 'voice_not_ready', false],
    [422, 'bad_audio', false],
    [413, 'chunk_too_large', false],
    [503, 'something_new', true],
    [400, 'something_new', false],
  ])('reports a %i %s refusal with retryable=%s', async (status, code, retryable) => {
    api.audioReplies.push({ fail: refusal(status, code) });

    await uploadClosed(4);

    expect(listener.failure).toMatchObject({ code, retryable });
    expect(api.log).toEqual(['create', 'audio d1#0']);
  });

  it('refuses and deletes a dictation opened at another sample rate', async () => {
    api.sampleRate = 48_000;

    await uploadClosed(4);

    expect(api.log).toEqual(['create', 'remove d1']);
    expect(listener.failure).toEqual({ code: 'generic', retryable: false, message: null });
  });

  it('never aborts a create on cancel and deletes the id it returns', async () => {
    const create = new Held<VoiceDictationOpened>();
    api.createReplies.push({ hold: create });
    buffer.append(ramp(4));
    const upload = uploader();
    upload.start();
    await settle();

    upload.cancel();
    await settle();
    expect(create.signal?.aborted).toBe(false);

    create.resolve(api.opened('d7'));
    await settle();

    expect(api.log).toEqual(['create', 'remove d7']);
    expect(listener.events).toEqual([]);
  });

  it('aborts the chunk in flight on cancel, deletes the dictation and reports nothing', async () => {
    const chunk = new Held<undefined>();
    api.audioReplies.push({ hold: chunk });
    buffer.append(ramp(4));
    const upload = uploader();
    upload.start();
    await settle();

    upload.cancel();
    await settle();

    expect(chunk.signal?.aborted).toBe(true);
    expect(api.log).toEqual(['create', 'audio d1#0', 'remove d1']);
    expect(listener.events).toEqual([]);
    expect(upload.retry()).toBe(false);
  });

  it('swallows a failing DELETE on cancel', async () => {
    api.removeFailure = networkError();
    buffer.append(ramp(4));
    const upload = uploader();
    upload.start();
    await settle();

    upload.cancel();
    await settle();

    expect(api.log.at(-1)).toBe('remove d1');
    expect(listener.events).toEqual([]);
  });
});

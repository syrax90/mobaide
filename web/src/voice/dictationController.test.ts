import { beforeEach, describe, expect, it } from 'vitest';
import {
  FakeClock,
  FakeRecorder,
  FakeVoiceApi,
  refusal,
  settle,
} from './__testing__/fakeVoiceApi';
import { DictationController, RecorderError, type DictationState } from './dictationController';

const RATE = 16_000;

let api: FakeVoiceApi;
let recorder: FakeRecorder;
let states: DictationState[];

beforeEach(() => {
  api = new FakeVoiceApi();
  api.maxChunkBytes = 32_000;
  recorder = new FakeRecorder();
  states = [];
});

function controller(maxSeconds = 120): DictationController {
  return new DictationController({
    api,
    createRecorder: () => recorder,
    sampleRate: RATE,
    maxSeconds,
    onChange: (state) => states.push(state),
    clock: new FakeClock(),
  });
}

function speak(seconds: number): void {
  recorder.sink?.samples(Int16Array.from({ length: seconds * RATE }, (_, i) => (i % 200) - 100));
}

const kinds = (): string[] => states.map((state) => state.kind).filter((k, i, all) => k !== all[i - 1]);

async function recording(maxSeconds?: number): Promise<DictationController> {
  const dictation = controller(maxSeconds);
  await dictation.start();
  await settle();
  return dictation;
}

describe('DictationController', () => {
  it('records, transcribes on stop and hands the text back as a result', async () => {
    const dictation = await recording();
    speak(1);
    recorder.sink?.level(0.4);

    expect(dictation.current).toEqual({
      kind: 'recording',
      elapsedMs: 1_000,
      level: 0.4,
      remainingSeconds: null,
      lagging: false,
    });

    dictation.toggle();
    await settle();

    expect(kinds()).toEqual(['starting', 'recording', 'transcribing', 'result']);
    expect(dictation.current).toEqual({ kind: 'result', text: 'hello world' });
    expect(recorder.sampleRate).toBe(RATE);
    expect(recorder.stops).toBe(1);
    expect(api.log).toEqual(['create', 'audio d1#0', 'finish d1@0']);
  });

  it('reads an empty transcript as empty', async () => {
    api.text = '';
    const dictation = await recording();
    speak(1);

    await dictation.stop();
    await settle();

    expect(dictation.current).toEqual({ kind: 'empty' });
  });

  it('counts down the last 15 s from the samples recorded', async () => {
    const dictation = await recording(20);

    speak(4);
    expect(dictation.current).toMatchObject({ kind: 'recording', remainingSeconds: null });

    speak(2);
    expect(dictation.current).toMatchObject({
      kind: 'recording',
      elapsedMs: 6_000,
      remainingSeconds: 14,
    });
  });

  it('stops by itself at the limit and still transcribes', async () => {
    const dictation = await recording(2);

    speak(2);

    expect(dictation.current).toEqual({ kind: 'transcribing', interruptedBy: 'limit', lagging: false });
    await settle();
    expect(dictation.current).toEqual({ kind: 'result', text: 'hello world' });
  });

  it('transcribes what was recorded when the recorder is interrupted', async () => {
    const dictation = await recording();
    speak(1);

    recorder.sink?.interrupted('background');

    expect(dictation.current).toEqual({
      kind: 'transcribing',
      interruptedBy: 'background',
      lagging: false,
    });
    await settle();
    expect(dictation.current.kind).toBe('result');
  });

  it('fails without audio when the microphone is refused, and deletes the opened dictation', async () => {
    recorder.startFailure = new RecorderError('mic_permission_denied');
    const dictation = controller();

    await dictation.start();
    await settle();

    expect(dictation.current).toEqual({
      kind: 'failed',
      code: 'mic_permission_denied',
      retryable: false,
      message: 'mic_permission_denied',
      hasAudio: false,
    });
    expect(api.log).toEqual(['create', 'remove d1']);
  });

  it('keeps the audio after an upload failure so Retry needs no new recording', async () => {
    api.finishReplies.push({ fail: refusal(503, 'transcription_failed') });
    const dictation = await recording();
    speak(1);
    await dictation.stop();
    await settle();

    expect(dictation.current).toMatchObject({
      kind: 'failed',
      code: 'transcription_failed',
      retryable: true,
      hasAudio: true,
    });

    dictation.retry();
    expect(dictation.current).toEqual({ kind: 'transcribing', interruptedBy: null, lagging: false });
    await settle();

    expect(dictation.current).toEqual({ kind: 'result', text: 'hello world' });
    expect(api.log.slice(3)).toEqual(['remove d1', 'create', 'audio d2#0', 'finish d2@0']);
  });

  it('ignores Retry for a failure that cannot succeed again', async () => {
    api.audioReplies.push({ fail: refusal(422, 'bad_audio') });
    const dictation = await recording();
    speak(1);
    await settle();
    expect(dictation.current).toMatchObject({ kind: 'failed', code: 'bad_audio', retryable: false });

    dictation.retry();

    expect(dictation.current.kind).toBe('failed');
  });

  it('cancels a recording: back to idle, the dictation deleted, nothing transcribed', async () => {
    const dictation = await recording();
    speak(1);
    await settle();

    dictation.cancel();
    await settle();

    expect(dictation.current).toEqual({ kind: 'idle' });
    expect(recorder.stops).toBe(1);
    expect(api.log).toEqual(['create', 'audio d1#0', 'remove d1']);
  });

  it('abandons a start whose microphone is still opening', async () => {
    let openMic = (): void => undefined;
    recorder.startGate = new Promise<void>((resolve) => {
      openMic = resolve;
    });
    const dictation = controller();
    const starting = dictation.start();
    await settle();
    expect(dictation.current).toEqual({ kind: 'starting' });

    dictation.toggle();
    openMic();
    await starting;
    await settle();

    expect(dictation.current).toEqual({ kind: 'idle' });
    expect(recorder.stops).toBeGreaterThanOrEqual(1);
    expect(api.log).toEqual(['create', 'remove d1']);
  });

  it('dismisses a result back to idle', async () => {
    const dictation = await recording();
    speak(1);
    await dictation.stop();
    await settle();

    dictation.dismiss();

    expect(dictation.current).toEqual({ kind: 'idle' });
  });

  it('cancels on dispose and starts nothing afterwards', async () => {
    const dictation = await recording();
    speak(1);

    dictation.dispose();
    await dictation.start();
    await settle();

    expect(dictation.current).toEqual({ kind: 'idle' });
    expect(api.log.filter((entry) => entry === 'create')).toHaveLength(1);
    expect(api.log.at(-1)).toBe('remove d1');
  });
});

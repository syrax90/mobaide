import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  FakeClock,
  FakeRecorder,
  FakeVoiceApi,
  Held,
  answer,
  networkError,
  refusal,
  settle,
  type Reply,
} from './__testing__/fakeVoiceApi';
import {
  DictationController,
  RecorderError,
  runCleanup,
  type DictationState,
  type TranscriptCleaner,
} from './dictationController';

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

function controller(
  maxSeconds = 120,
  cleaner?: () => TranscriptCleaner | null,
): DictationController {
  return new DictationController({
    api,
    createRecorder: () => recorder,
    sampleRate: RATE,
    maxSeconds,
    onChange: (state) => states.push(state),
    clock: new FakeClock(),
    cleaner,
  });
}

function speak(seconds: number): void {
  recorder.sink?.samples(Int16Array.from({ length: seconds * RATE }, (_, i) => (i % 200) - 100));
}

const kinds = (): string[] => states.map((state) => state.kind).filter((k, i, all) => k !== all[i - 1]);

async function recording(
  maxSeconds?: number,
  cleaner?: () => TranscriptCleaner | null,
): Promise<DictationController> {
  const dictation = controller(maxSeconds, cleaner);
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

/** The session's AI agent on the agent's computer, answering `reply`; every request is kept. */
class FakeCleaner {
  readonly calls: { text: string; signal: AbortSignal }[] = [];
  reply: Reply<string> = { ok: 'Hello, world.' };

  readonly clean: TranscriptCleaner = (text, signal) => {
    this.calls.push({ text, signal });
    return answer(this.reply, signal);
  };
}

describe('DictationController cleanup', () => {
  let cleaner: FakeCleaner;

  beforeEach(() => {
    cleaner = new FakeCleaner();
  });

  async function cleaning(): Promise<DictationController> {
    const dictation = await recording(120, () => cleaner.clean);
    speak(1);
    await dictation.stop();
    await settle();
    return dictation;
  }

  it('cleans the transcript up before handing it back, keeping the transcript as heard', async () => {
    const dictation = await cleaning();

    expect(kinds()).toEqual(['starting', 'recording', 'transcribing', 'cleaning', 'result']);
    expect(states.find((state) => state.kind === 'cleaning')).toEqual({
      kind: 'cleaning',
      text: 'hello world',
    });
    expect(dictation.current).toEqual({
      kind: 'result',
      text: 'Hello, world.',
      original: 'hello world',
    });
    expect(cleaner.calls.map((call) => call.text)).toEqual(['hello world']);
  });

  it.each([
    ['unchanged', 'hello world'],
  ])('hands back a plain result when the cleanup comes back %s', async (_label, cleaned) => {
    cleaner.reply = { ok: cleaned };

    const dictation = await cleaning();

    expect(dictation.current).toStrictEqual({ kind: 'result', text: 'hello world' });
  });

  it.each([
    [
      'a coded refusal',
      refusal(503, 'cleanup_outdated', { detailMessage: 'Run claude update.' }),
      { code: 'cleanup_outdated', message: 'Run claude update.' },
    ],
    ['a dropped connection', networkError(), { code: 'network', message: 'offline' }],
  ])('shows the transcript as heard after %s, saying why', async (_label, error, cleanupError) => {
    cleaner.reply = { fail: error };

    const dictation = await cleaning();

    expect(dictation.current).toEqual({ kind: 'result', text: 'hello world', cleanupError });
  });

  it('waits past the relay timeout, then gives up at 35 s with the transcript as heard', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      cleaner.reply = { hold: new Held<string>() };
      const dictation = await cleaning();

      await vi.advanceTimersByTimeAsync(30_000);
      expect(dictation.current.kind).toBe('cleaning');

      await vi.advanceTimersByTimeAsync(5_000);
      await settle();

      expect(cleaner.calls[0]?.signal.aborted).toBe(true);
      expect(dictation.current).toEqual({
        kind: 'result',
        text: 'hello world',
        cleanupError: { code: 'cleanup_timeout', message: null },
      });
    } finally {
      vi.useRealTimers();
    }
  });

  it('cancels a cleanup: back to idle, the request aborted', async () => {
    cleaner.reply = { hold: new Held<string>() };
    const dictation = await cleaning();

    dictation.cancel();
    await settle();

    expect(cleaner.calls[0]?.signal.aborted).toBe(true);
    expect(dictation.current).toEqual({ kind: 'idle' });
  });

  it('ignores the mic while cleaning up', async () => {
    cleaner.reply = { hold: new Held<string>() };
    const dictation = await cleaning();

    dictation.toggle();
    await dictation.start();
    await settle();

    expect(dictation.current).toEqual({ kind: 'cleaning', text: 'hello world' });
    expect(api.log.filter((entry) => entry === 'create')).toHaveLength(1);
  });

  it('never lets an abandoned cleanup answer into the next dictation', async () => {
    // A reply already on its way when the user cancels ignores the abort.
    const answers: ((text: string) => void)[] = [];
    const deaf: TranscriptCleaner = () =>
      new Promise<string>((resolve) => {
        answers.push(resolve);
      });
    const dictation = await recording(120, () => deaf);
    speak(1);
    await dictation.stop();
    await settle();
    dictation.cancel();
    await dictation.start();
    await settle();
    speak(1);
    await dictation.stop();
    await settle();

    answers[0]?.('Stale.');
    await settle();
    expect(dictation.current).toEqual({ kind: 'cleaning', text: 'hello world' });

    answers[1]?.('Fresh.');
    await settle();
    expect(dictation.current).toEqual({ kind: 'result', text: 'Fresh.', original: 'hello world' });
  });

  it('reads the cleanup setting when the transcript arrives, not when recording starts', async () => {
    let cleanUp = false;
    const dictation = await recording(120, () => (cleanUp ? cleaner.clean : null));
    speak(1);

    cleanUp = true;
    await dictation.stop();
    await settle();

    expect(dictation.current).toEqual({
      kind: 'result',
      text: 'Hello, world.',
      original: 'hello world',
    });
  });

  it('sends nothing to clean up when nothing was heard', async () => {
    api.text = '';

    const dictation = await cleaning();

    expect(dictation.current).toEqual({ kind: 'empty' });
    expect(cleaner.calls).toEqual([]);
  });
});

describe('runCleanup', () => {
  let cleaner: FakeCleaner;

  beforeEach(() => {
    cleaner = new FakeCleaner();
  });

  it('hands back the cleaned-up text, trimmed', async () => {
    cleaner.reply = { ok: '  Fix the bug.\n' };

    const outcome = await runCleanup(cleaner.clean, 'um fix the uh bug', new AbortController());

    expect(outcome).toEqual({ kind: 'cleaned', text: 'Fix the bug.' });
    expect(cleaner.calls.map((call) => call.text)).toEqual(['um fix the uh bug']);
  });

  it.each([
    ['the text as it was, padded differently', ' hello world ', 'hello world'],
    ['nothing', 'hello world', '  '],
  ])('reads %s as unchanged', async (_label, text, cleaned) => {
    cleaner.reply = { ok: cleaned };

    const outcome = await runCleanup(cleaner.clean, text, new AbortController());

    expect(outcome).toStrictEqual({ kind: 'unchanged' });
  });

  it('turns a refusal into a failure instead of throwing', async () => {
    cleaner.reply = { fail: refusal(409, 'cleanup_busy', { detailMessage: 'Two cleanups are running.' }) };

    const outcome = await runCleanup(cleaner.clean, 'hello world', new AbortController());

    expect(outcome).toEqual({
      kind: 'failed',
      error: { code: 'cleanup_busy', message: 'Two cleanups are running.' },
    });
  });

  it('gives up at 35 s, aborting the request it was given', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      cleaner.reply = { hold: new Held<string>() };
      const abort = new AbortController();
      const outcome = runCleanup(cleaner.clean, 'hello world', abort);

      await vi.advanceTimersByTimeAsync(35_000);

      expect(abort.signal.aborted).toBe(true);
      await expect(outcome).resolves.toEqual({
        kind: 'failed',
        error: { code: 'cleanup_timeout', message: null },
      });
    } finally {
      vi.useRealTimers();
    }
  });
});

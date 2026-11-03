/**
 * Shared doubles for the dictation uploader and controller tests.
 *
 * Test-only, same rule as `realtime/__testing__/fakeSocket.ts`: nothing under
 * `src/` outside a `*.test.ts(x)` may import it. Both suites drive the same
 * uploader against the same scripted agent, so one copy keeps them in step.
 */
import type { Recorder, RecorderSink } from '../dictationController';
import type { UploaderClock, UploaderListener, UploadFailure } from '../dictationUploader';
import {
  VoiceApiError,
  type VoiceApi,
  type VoiceDictationOpened,
  type VoiceFinishResult,
} from '../voiceApi';

/** A request the test answers by hand; it rejects like `fetch` on abort. */
export class Held<T> {
  signal: AbortSignal | null = null;
  private resolveFn: ((value: T) => void) | null = null;
  private rejectFn: ((error: unknown) => void) | null = null;

  get entered(): boolean {
    return this.signal !== null;
  }

  bind(signal: AbortSignal, resolve: (value: T) => void, reject: (error: unknown) => void): void {
    this.signal = signal;
    this.resolveFn = resolve;
    this.rejectFn = reject;
  }

  resolve(value: T): void {
    this.resolveFn?.(value);
  }

  reject(error: unknown): void {
    this.rejectFn?.(error);
  }
}

export type Reply<T> = { ok: T } | { fail: Error } | { hold: Held<T> };

export const networkError = (): VoiceApiError =>
  new VoiceApiError({ status: null, code: null, message: 'offline', network: true });

export const refusal = (
  status: number,
  code: string | null,
  extra: { detailMessage?: string; expectedSeq?: number } = {},
): VoiceApiError =>
  new VoiceApiError({
    status,
    code,
    message: `HTTP ${String(status)}`,
    detailMessage: extra.detailMessage ?? null,
    expectedSeq: extra.expectedSeq ?? null,
  });

/**
 * The agent's dictation API, scripted per method: queued replies first, then
 * the method's fallback. Every call lands in `log` (`create`, `audio d1#0`,
 * `finish d1@2`, `remove d1`) so a test reads the request order directly.
 */
export class FakeVoiceApi implements VoiceApi {
  readonly log: string[] = [];
  readonly payloads = new Map<string, string>();
  readonly createSignals: AbortSignal[] = [];
  readonly createReplies: Reply<VoiceDictationOpened>[] = [];
  readonly audioReplies: Reply<undefined>[] = [];
  readonly finishReplies: Reply<VoiceFinishResult>[] = [];
  sampleRate = 16_000;
  maxSeconds = 120;
  maxChunkBytes = 8;
  text = 'hello world';
  audioFallback: () => Reply<undefined> = () => ({ ok: undefined });
  finishFallback: () => Reply<VoiceFinishResult> = () => ({
    ok: { state: 'done', text: this.text },
  });
  removeFailure: Error | null = null;
  private created = 0;

  opened(dictationId: string): VoiceDictationOpened {
    return {
      dictationId,
      sampleRate: this.sampleRate,
      maxSeconds: this.maxSeconds,
      maxChunkBytes: this.maxChunkBytes,
    };
  }

  create(signal: AbortSignal): Promise<VoiceDictationOpened> {
    this.log.push('create');
    this.createSignals.push(signal);
    this.created += 1;
    const id = `d${String(this.created)}`;
    return answer(this.createReplies.shift() ?? { ok: this.opened(id) }, signal);
  }

  audio(dictationId: string, seq: number, pcm16B64: string, signal: AbortSignal): Promise<void> {
    this.log.push(`audio ${dictationId}#${String(seq)}`);
    this.payloads.set(`${dictationId}#${String(seq)}`, pcm16B64);
    return answer(this.audioReplies.shift() ?? this.audioFallback(), signal);
  }

  finish(dictationId: string, lastSeq: number, signal: AbortSignal): Promise<VoiceFinishResult> {
    this.log.push(`finish ${dictationId}@${String(lastSeq)}`);
    return answer(this.finishReplies.shift() ?? this.finishFallback(), signal);
  }

  remove(dictationId: string): Promise<void> {
    this.log.push(`remove ${dictationId}`);
    return this.removeFailure === null ? Promise.resolve() : Promise.reject(this.removeFailure);
  }
}

export function answer<T>(reply: Reply<T>, signal: AbortSignal): Promise<T> {
  if ('ok' in reply) return Promise.resolve(reply.ok);
  if ('fail' in reply) return Promise.reject(reply.fail);
  const held = reply.hold;
  return new Promise<T>((resolve, reject) => {
    held.bind(signal, resolve, reject);
    signal.addEventListener(
      'abort',
      () => {
        reject(new DOMException('The operation was aborted.', 'AbortError'));
      },
      { once: true },
    );
  });
}

/** Time only moves when the uploader sleeps; every sleep is recorded. */
export class FakeClock implements UploaderClock {
  nowMs = 0;
  randomValue = 0.5;
  readonly sleeps: number[] = [];

  now(): number {
    return this.nowMs;
  }

  sleep(ms: number): Promise<void> {
    this.sleeps.push(ms);
    this.nowMs += ms;
    return Promise.resolve();
  }

  random(): number {
    return this.randomValue;
  }
}

export class RecordingListener implements UploaderListener {
  readonly events: string[] = [];
  failure: UploadFailure | null = null;

  lagging(lagging: boolean): void {
    this.events.push(`lagging ${String(lagging)}`);
  }

  stopCapture(): void {
    this.events.push('stopCapture');
  }

  done(text: string): void {
    this.events.push(`done ${text}`);
  }

  failed(failure: UploadFailure): void {
    this.events.push(`failed ${failure.code}`);
    this.failure = failure;
  }
}

/** The microphone, driven by the test through `sink`. */
export class FakeRecorder implements Recorder {
  sink: RecorderSink | null = null;
  sampleRate: number | null = null;
  stops = 0;
  startFailure: Error | null = null;
  startGate: Promise<void> | null = null;

  async start(sampleRate: number, sink: RecorderSink): Promise<void> {
    this.sampleRate = sampleRate;
    this.sink = sink;
    if (this.startGate !== null) await this.startGate;
    if (this.startFailure !== null) throw this.startFailure;
  }

  stop(): Promise<void> {
    this.stops += 1;
    return Promise.resolve();
  }
}

/** Let every pending promise chain run to its next real wait. */
export async function settle(): Promise<void> {
  await new Promise<void>((resolve) => setTimeout(resolve, 0));
}

/** `count` distinct, non-zero samples. */
export function ramp(count: number, from = 1): Int16Array {
  return Int16Array.from({ length: count }, (_, i) => from + i);
}

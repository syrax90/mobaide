import { chunkBytes } from './pcm16';
import type { PcmBuffer } from './pcmBuffer';
import { VOICE_CODE, VoiceApiError, type VoiceApi, type VoiceDictationOpened } from './voiceApi';

export interface UploadFailure {
  code: string;
  /** Whether a Retry (from the buffered audio) can succeed. */
  retryable: boolean;
  /** The agent's raw message, shown only behind "Details". */
  message: string | null;
}

export interface UploaderListener {
  lagging(lagging: boolean): void;
  /** The agent's limit was reached: stop recording and transcribe. */
  stopCapture(): void;
  done(text: string): void;
  failed(failure: UploadFailure): void;
}

export interface UploaderClock {
  now(): number;
  /** Resolves after `ms`, or at once when `signal` aborts. */
  sleep(ms: number, signal: AbortSignal): Promise<void>;
  random(): number;
}

export const systemClock: UploaderClock = {
  now: () => Date.now(),
  sleep: (ms, signal) =>
    new Promise((resolve) => {
      if (signal.aborted) {
        resolve();
        return;
      }
      const finish = () => {
        clearTimeout(timer);
        signal.removeEventListener('abort', finish);
        resolve();
      };
      const timer = setTimeout(finish, ms);
      signal.addEventListener('abort', finish, { once: true });
    }),
  random: () => Math.random(),
};

/** Above the relay's 30 s wait, so an id the agent did issue is never thrown away. */
const CREATE_TIMEOUT_MS = 35_000;
const AUDIO_TIMEOUT_MS = 15_000;
/** The agent waits up to 20 s and the relay 30 s before answering `finish`. */
const FINISH_TIMEOUT_MS = 35_000;
/** After the user stops, how long a stalled upload may make no progress. */
const STALL_LIMIT_MS = 30_000;
/** The agent keeps a finished result for 2 minutes. */
const FINISH_DEADLINE_MS = 120_000;
const MIN_FINISH_POLL_MS = 1_000;
const MAX_RESTARTS = 2;
const MAX_RESYNCS = 3;
const BACKOFF_MS = [500, 1_000, 2_000, 4_000] as const;

class Fatal extends Error {
  readonly failure: UploadFailure;

  constructor(failure: UploadFailure) {
    super(failure.code);
    this.failure = failure;
  }
}

class Cancelled extends Error {}

const RESYNC = Symbol('resync');

interface Classified {
  transient: boolean;
  status: number | null;
  code: string | null;
  message: string | null;
  expectedSeq: number | null;
}

/**
 * Streams one dictation's audio to the agent and fetches its transcript.
 *
 * A single pump is the only code that issues requests, so exactly one audio
 * request is in flight per dictation and `finish` is only ever sent after
 * the last chunk was acknowledged. `onAudio`, `close` and `cancel` only set
 * state and wake the pump.
 *
 * - Network failures and uncoded 5xx retry the same request with backoff;
 *   recording continues into the buffer meanwhile. Once the user has
 *   stopped, 30 s without progress fails the upload (retryable: the audio is
 *   still buffered).
 * - `seq_gap` resumes at the agent's `expected_seq`; `dictation_not_found`
 *   (expired after 60 s idle or 5 min open) opens a new dictation and
 *   re-uploads everything from seq 0. Both are bounded.
 * - `too_long` stops capture and finishes with the last accepted chunk.
 * - `transcription_failed` is latched by the agent for the dictation: it is
 *   never retried automatically, and an explicit retry opens a new one.
 * - `finish` answering `transcribing` is simply asked again.
 */
export class DictationUploader {
  private dictationId: string | null = null;
  private chunkSamples = 0;
  private nextSeq = 0;
  /** Highest seq the agent answered 200 for: what `finish` reports. */
  private accepted = -1;
  private closed = false;
  private closedAt: number | null = null;
  private serverCap = false;
  private restarts = 0;
  private resyncs = 0;
  private latched = false;
  private cancelled = false;
  private running = false;
  private isLagging = false;
  private wakeRequested = false;
  private wakeWaiter: (() => void) | null = null;
  private readonly cancelController = new AbortController();
  private readonly api: VoiceApi;
  private readonly buffer: PcmBuffer;
  private readonly sampleRate: number;
  private readonly listener: UploaderListener;
  private readonly clock: UploaderClock;

  constructor(
    api: VoiceApi,
    buffer: PcmBuffer,
    sampleRate: number,
    listener: UploaderListener,
    clock: UploaderClock = systemClock,
  ) {
    this.api = api;
    this.buffer = buffer;
    this.sampleRate = sampleRate;
    this.listener = listener;
    this.clock = clock;
  }

  start(): void {
    this.launch();
  }

  /** New samples are in the buffer. */
  onAudio(): void {
    this.wake();
  }

  /** Recording stopped: upload the tail and transcribe. */
  close(): void {
    if (!this.closed) {
      this.closed = true;
      this.closedAt = this.clock.now();
    }
    this.wake();
  }

  /**
   * Resume after a retryable failure, from the buffered audio. A live
   * dictation continues where it stopped (or re-asks `finish`, whose result
   * the agent keeps); a latched or never-opened one starts over.
   */
  retry(): boolean {
    if (this.running || this.cancelled) return false;
    this.restarts = 0;
    this.resyncs = 0;
    if (this.latched || this.dictationId === null) {
      this.removeQuietly(this.dictationId);
      this.dictationId = null;
      this.nextSeq = 0;
      this.accepted = -1;
      this.latched = false;
      this.serverCap = false;
    }
    this.closed = true;
    this.closedAt = this.clock.now();
    this.launch();
    return true;
  }

  /** Abort everything and delete the dictation on the agent; never reports. */
  cancel(): void {
    if (this.cancelled) return;
    this.cancelled = true;
    this.cancelController.abort();
    this.wake();
    this.removeQuietly(this.dictationId);
    this.dictationId = null;
  }

  private launch(): void {
    this.running = true;
    void this.run().finally(() => {
      this.running = false;
    });
  }

  private async run(): Promise<void> {
    try {
      if (this.dictationId === null) await this.open();
      for (;;) {
        this.throwIfCancelled();
        const final = this.closed || this.serverCap;
        const sealed = this.buffer.sealedChunks(this.chunkSamples, final);
        if (!this.serverCap && this.nextSeq < sealed) {
          await this.send(this.nextSeq);
          continue;
        }
        if (final) {
          const outcome = await this.finishLoop();
          if (outcome === RESYNC) continue;
          this.throwIfCancelled();
          this.listener.done(outcome);
          return;
        }
        await this.waitForWake();
      }
    } catch (error) {
      if (this.cancelled || error instanceof Cancelled) return;
      this.listener.failed(
        error instanceof Fatal
          ? error.failure
          : {
              code: VOICE_CODE.generic,
              retryable: true,
              message: error instanceof Error ? error.message : null,
            },
      );
    }
  }

  private async open(): Promise<void> {
    let attempt = 0;
    let firstFailure: number | null = null;
    for (;;) {
      this.throwIfCancelled();
      let opened: VoiceDictationOpened;
      try {
        // Never aborted by a cancel: the id it returns must reach a DELETE,
        // or the dictation would hold one of the agent's two slots.
        opened = await this.call(CREATE_TIMEOUT_MS, false, (signal) => this.api.create(signal));
      } catch (error) {
        const failure = this.classify(error);
        if (failure.transient) {
          firstFailure ??= this.clock.now();
          this.checkStall(firstFailure, failure);
          this.setLagging(true);
          await this.backoff(attempt++);
          continue;
        }
        throw this.fatal(failure);
      }
      if (this.cancelled) {
        this.removeQuietly(opened.dictationId);
        throw new Cancelled();
      }
      const bytes =
        opened.sampleRate === this.sampleRate
          ? chunkBytes(opened.sampleRate, opened.maxChunkBytes)
          : null;
      if (bytes === null) {
        this.removeQuietly(opened.dictationId);
        throw new Fatal({ code: VOICE_CODE.generic, retryable: false, message: null });
      }
      this.chunkSamples = bytes / 2;
      this.buffer.shrinkCapacity(opened.maxSeconds * opened.sampleRate);
      this.dictationId = opened.dictationId;
      this.setLagging(false);
      if (this.buffer.isFull && !this.closed) this.listener.stopCapture();
      return;
    }
  }

  private async send(seq: number): Promise<void> {
    const dictationId = this.requireDictation();
    const payload = this.buffer.payload(seq, this.chunkSamples);
    let attempt = 0;
    let firstFailure: number | null = null;
    for (;;) {
      try {
        await this.call(AUDIO_TIMEOUT_MS, true, (signal) =>
          this.api.audio(dictationId, seq, payload, signal),
        );
        this.accepted = seq;
        this.nextSeq = seq + 1;
        this.setLagging(false);
        return;
      } catch (error) {
        const failure = this.classify(error);
        if (failure.transient) {
          firstFailure ??= this.clock.now();
          this.checkStall(firstFailure, failure);
          this.setLagging(true);
          await this.backoff(attempt++);
          continue;
        }
        switch (failure.code) {
          case VOICE_CODE.dictationNotFound:
            await this.restart();
            return;
          case VOICE_CODE.seqGap:
            this.resync(failure.expectedSeq);
            return;
          case VOICE_CODE.tooLong:
            this.serverCap = true;
            this.listener.stopCapture();
            return;
          case VOICE_CODE.transcriptionFailed:
            this.latched = true;
            throw this.fatal(failure);
          default:
            throw this.fatal(failure);
        }
      }
    }
  }

  private async finishLoop(): Promise<string | typeof RESYNC> {
    const dictationId = this.requireDictation();
    const deadline = this.clock.now() + FINISH_DEADLINE_MS;
    let attempt = 0;
    let firstFailure: number | null = null;
    for (;;) {
      this.throwIfCancelled();
      if (this.clock.now() > deadline) {
        throw new Fatal({ code: VOICE_CODE.transcriptionTimeout, retryable: true, message: null });
      }
      const askedAt = this.clock.now();
      try {
        const result = await this.call(FINISH_TIMEOUT_MS, true, (signal) =>
          this.api.finish(dictationId, this.accepted, signal),
        );
        this.setLagging(false);
        if (result.state === 'done') return (result.text ?? '').trim();
        attempt = 0;
        firstFailure = null;
        // `transcribing` normally comes after the agent's own 20 s wait; an
        // instant answer must not turn this into a hot loop.
        const elapsed = this.clock.now() - askedAt;
        if (elapsed < MIN_FINISH_POLL_MS) {
          await this.clock.sleep(MIN_FINISH_POLL_MS - elapsed, this.cancelController.signal);
        }
      } catch (error) {
        const failure = this.classify(error);
        if (failure.transient) {
          firstFailure ??= this.clock.now();
          this.checkStall(firstFailure, failure);
          this.setLagging(true);
          await this.backoff(attempt++);
          continue;
        }
        switch (failure.code) {
          case VOICE_CODE.seqGap:
            this.resync(failure.expectedSeq);
            return RESYNC;
          case VOICE_CODE.dictationNotFound:
            await this.restart();
            return RESYNC;
          case VOICE_CODE.transcriptionFailed:
            this.latched = true;
            throw this.fatal(failure);
          default:
            throw this.fatal(failure);
        }
      }
    }
  }

  private resync(expected: number | null): void {
    this.resyncs += 1;
    if (
      this.resyncs > MAX_RESYNCS ||
      expected === null ||
      expected < 0 ||
      expected > this.nextSeq
    ) {
      throw new Fatal({ code: VOICE_CODE.seqGap, retryable: false, message: null });
    }
    this.nextSeq = expected;
    this.accepted = expected - 1;
  }

  private async restart(): Promise<void> {
    this.restarts += 1;
    if (this.restarts > MAX_RESTARTS) {
      throw new Fatal({ code: VOICE_CODE.dictationNotFound, retryable: true, message: null });
    }
    this.removeQuietly(this.dictationId);
    this.dictationId = null;
    this.nextSeq = 0;
    this.accepted = -1;
    this.resyncs = 0;
    this.serverCap = false;
    await this.open();
  }

  private async call<T>(
    timeoutMs: number,
    cancellable: boolean,
    fn: (signal: AbortSignal) => Promise<T>,
  ): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      controller.abort();
    }, timeoutMs);
    const onCancel = () => {
      controller.abort();
    };
    const cancelSignal = this.cancelController.signal;
    if (cancellable) cancelSignal.addEventListener('abort', onCancel, { once: true });
    try {
      return await fn(controller.signal);
    } finally {
      clearTimeout(timer);
      cancelSignal.removeEventListener('abort', onCancel);
    }
  }

  /** Every failure while cancelled ends the pump silently. */
  private classify(error: unknown): Classified {
    this.throwIfCancelled();
    if (error instanceof VoiceApiError) {
      const transient =
        error.network ||
        (error.status !== null &&
          error.status >= 500 &&
          (error.code === null || error.code === VOICE_CODE.busy));
      return {
        transient,
        status: error.status,
        code: error.code,
        message: error.detailMessage ?? error.message,
        expectedSeq: error.expectedSeq,
      };
    }
    // A timeout abort or a fetch that never got an answer.
    return {
      transient: true,
      status: null,
      code: null,
      message: error instanceof Error ? error.message : null,
      expectedSeq: null,
    };
  }

  private fatal(failure: Classified): Fatal {
    return new Fatal({
      code: failure.code ?? (failure.transient ? VOICE_CODE.network : VOICE_CODE.generic),
      retryable: isRetryable(failure),
      message: failure.message,
    });
  }

  private checkStall(firstFailure: number, failure: Classified): void {
    if (this.closedAt === null) return;
    const since = Math.max(firstFailure, this.closedAt);
    if (this.clock.now() - since > STALL_LIMIT_MS) {
      throw new Fatal({ code: VOICE_CODE.network, retryable: true, message: failure.message });
    }
  }

  private async backoff(attempt: number): Promise<void> {
    const base = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)] ?? 4_000;
    const jitter = base * 0.2 * (this.clock.random() * 2 - 1);
    await this.clock.sleep(base + jitter, this.cancelController.signal);
    this.throwIfCancelled();
  }

  private async waitForWake(): Promise<void> {
    if (!this.wakeRequested) {
      await new Promise<void>((resolve) => {
        this.wakeWaiter = resolve;
      });
    }
    this.wakeRequested = false;
  }

  private wake(): void {
    this.wakeRequested = true;
    const waiter = this.wakeWaiter;
    this.wakeWaiter = null;
    waiter?.();
  }

  private setLagging(lagging: boolean): void {
    if (this.isLagging === lagging) return;
    this.isLagging = lagging;
    this.listener.lagging(lagging);
  }

  private requireDictation(): string {
    if (this.dictationId === null) throw new Fatal({ code: VOICE_CODE.generic, retryable: true, message: null });
    return this.dictationId;
  }

  private removeQuietly(dictationId: string | null): void {
    if (dictationId === null) return;
    // Fire and forget: the DELETE is idempotent, and an older relay drops the
    // connection after its 204, so even a "failure" usually means it worked.
    void this.api.remove(dictationId).catch(() => undefined);
  }

  private throwIfCancelled(): void {
    if (this.cancelled) throw new Cancelled();
  }
}

function isRetryable(failure: Classified): boolean {
  if (failure.transient) return true;
  switch (failure.code) {
    case VOICE_CODE.busy:
    case VOICE_CODE.transcriptionFailed:
    case VOICE_CODE.dictationNotFound:
      return true;
    case VOICE_CODE.notReady:
    case VOICE_CODE.unsupported:
    case VOICE_CODE.badAudio:
    case VOICE_CODE.chunkTooLarge:
    case VOICE_CODE.dictationFinished:
    case VOICE_CODE.seqGap:
    case VOICE_CODE.tooLong:
      return false;
    default:
      return failure.status !== null && failure.status >= 500;
  }
}

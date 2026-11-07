import { DictationUploader, systemClock, type UploadFailure, type UploaderClock } from './dictationUploader';
import { PcmBuffer } from './pcmBuffer';
import { VOICE_CODE, isVoiceApiError, type VoiceApi } from './voiceApi';

/** Why recording stopped on its own; the audio so far is still transcribed. */
export type InterruptReason = 'background' | 'device' | 'limit';

export type DictationState =
  | { kind: 'idle' }
  | { kind: 'starting' }
  | {
      kind: 'recording';
      elapsedMs: number;
      /** 0…1, for the level meter. */
      level: number;
      /** Seconds left before the agent's limit, once 15 or fewer remain. */
      remainingSeconds: number | null;
      /** Uploads are behind (network trouble); recording carries on. */
      lagging: boolean;
    }
  | { kind: 'transcribing'; interruptedBy: InterruptReason | null; lagging: boolean }
  /** The session's AI agent is cleaning up `text`, the transcript as heard. */
  | { kind: 'cleaning'; text: string }
  | {
      kind: 'result';
      text: string;
      /** The transcript as heard, when a cleanup changed it. */
      original?: string;
      /** Why the cleanup failed; `text` is then the transcript as heard. */
      cleanupError?: CleanupError;
    }
  | { kind: 'empty' }
  | {
      kind: 'failed';
      code: string;
      retryable: boolean;
      message: string | null;
      /** Audio is still buffered, so Retry needs no new recording. */
      hasAudio: boolean;
    };

export interface CleanupError {
  code: string;
  message: string | null;
}

/** Cleans up a transcript; rejects with a `VoiceApiError`. */
export type TranscriptCleaner = (text: string, signal: AbortSignal) => Promise<string>;

/** `unchanged` also covers an empty answer: the text stays as it was. */
export type CleanupOutcome =
  | { kind: 'cleaned'; text: string }
  | { kind: 'unchanged' }
  | { kind: 'failed'; error: CleanupError };

export interface RecorderSink {
  samples(samples: Int16Array): void;
  /** 0…1 */
  level(level: number): void;
  interrupted(reason: InterruptReason): void;
}

export class RecorderError extends Error {
  readonly code: typeof VOICE_CODE.micPermissionDenied | typeof VOICE_CODE.micUnavailable;

  constructor(
    code: typeof VOICE_CODE.micPermissionDenied | typeof VOICE_CODE.micUnavailable,
    message?: string,
  ) {
    super(message ?? code);
    this.name = 'RecorderError';
    this.code = code;
  }
}

/**
 * Platform audio capture at the agent's rate as PCM16. `start` rejects with a
 * `RecorderError`; `stop` is idempotent and delivers every pending sample
 * before it resolves.
 */
export interface Recorder {
  start(sampleRate: number, sink: RecorderSink): Promise<void>;
  stop(): Promise<void>;
}

export interface DictationControllerOptions {
  api: VoiceApi;
  createRecorder: () => Recorder;
  sampleRate: number;
  maxSeconds: number;
  onChange: (state: DictationState) => void;
  clock?: UploaderClock;
  /**
   * Read when a transcript arrives, so a setting changed while recording
   * applies to that transcript: how to clean it up, or null to show it as heard.
   */
  cleaner?: () => TranscriptCleaner | null;
}

const COUNTDOWN_SECONDS = 15;
/** Above the agent's 20 s budget and the relay's 30 s wait. */
const CLEANUP_TIMEOUT_MS = 35_000;

/**
 * One cleanup request, bounded by `CLEANUP_TIMEOUT_MS`; aborting `abort`
 * stops it. It never throws: a failure is an outcome, and the caller keeps
 * the text as it was.
 */
export async function runCleanup(
  cleaner: TranscriptCleaner,
  text: string,
  abort: AbortController,
): Promise<CleanupOutcome> {
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    abort.abort();
  }, CLEANUP_TIMEOUT_MS);
  try {
    const cleaned = (await cleaner(text, abort.signal)).trim();
    return cleaned === '' || cleaned === text.trim() ? { kind: 'unchanged' } : { kind: 'cleaned', text: cleaned };
  } catch (error) {
    return { kind: 'failed', error: cleanupError(error, timedOut) };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * One dictation at a time, as the UI sees it: microphone → buffer →
 * uploader → transcript. Android `DictationController.kt` and iOS
 * `DictationController.swift` have the same states and transitions.
 *
 * The transcript is handed to the UI as `result`; nothing here ever sends it.
 * Elapsed time is counted in samples, so the countdown and the agent's
 * `max_seconds` agree exactly.
 */
export class DictationController {
  private state: DictationState = { kind: 'idle' };
  /** Bumped by every new dictation and every cancel; stale callbacks compare it. */
  private generation = 0;
  private recorder: Recorder | null = null;
  private buffer: PcmBuffer | null = null;
  private uploader: DictationUploader | null = null;
  private cleanupAbort: AbortController | null = null;
  private level = 0;
  private lagging = false;
  private disposed = false;
  private readonly options: DictationControllerOptions;

  constructor(options: DictationControllerOptions) {
    this.options = options;
  }

  get current(): DictationState {
    return this.state;
  }

  /** The mic button: start, stop, or abandon a start that is still opening. */
  toggle(): void {
    switch (this.state.kind) {
      case 'recording':
        void this.stop();
        return;
      case 'starting':
        this.cancel();
        return;
      case 'transcribing':
      case 'cleaning':
        return;
      default:
        void this.start();
    }
  }

  /**
   * Runs synchronously up to the recorder's own first await, so a browser
   * still sees the click's user activation when the recorder opens audio.
   */
  async start(): Promise<void> {
    if (this.disposed) return;
    if (this.isActive()) return;
    this.discard();
    const generation = ++this.generation;
    const { api, sampleRate, maxSeconds, clock } = this.options;
    const buffer = new PcmBuffer(maxSeconds * sampleRate);
    const uploader = new DictationUploader(
      api,
      buffer,
      sampleRate,
      {
        lagging: (lagging) => {
          if (generation !== this.generation) return;
          this.lagging = lagging;
          this.publishProgress();
        },
        stopCapture: () => {
          if (generation !== this.generation) return;
          void this.stop('limit');
        },
        done: (text) => {
          if (generation !== this.generation) return;
          const cleaner = text === '' ? null : (this.options.cleaner?.() ?? null);
          if (cleaner === null) {
            this.finishWith(text === '' ? { kind: 'empty' } : { kind: 'result', text });
            return;
          }
          this.finishWith({ kind: 'cleaning', text });
          void this.cleanUp(generation, text, cleaner);
        },
        failed: (failure) => {
          if (generation !== this.generation) return;
          void this.fail(failure);
        },
      },
      clock ?? systemClock,
    );
    const recorder = this.options.createRecorder();
    this.buffer = buffer;
    this.uploader = uploader;
    this.recorder = recorder;
    this.level = 0;
    this.lagging = false;
    this.set({ kind: 'starting' });
    // The dictation opens while the microphone does, so the first words are
    // buffered instead of waiting for the round trip.
    uploader.start();
    try {
      await recorder.start(sampleRate, {
        samples: (samples) => {
          if (generation !== this.generation) return;
          buffer.append(samples);
          uploader.onAudio();
          if (this.state.kind !== 'recording') return;
          if (buffer.isFull) {
            void this.stop('limit');
            return;
          }
          this.publishProgress();
        },
        level: (level) => {
          if (generation !== this.generation) return;
          this.level = level;
          this.publishProgress();
        },
        interrupted: (reason) => {
          if (generation !== this.generation) return;
          void this.stop(reason);
        },
      });
    } catch (error) {
      // An upload failure may already have ended this dictation while the
      // microphone was still opening; that failure is the one to show.
      if (generation !== this.generation || this.kindNow() !== 'starting') return;
      this.recorder = null;
      uploader.cancel();
      buffer.release();
      this.buffer = null;
      this.uploader = null;
      this.set({
        kind: 'failed',
        code: error instanceof RecorderError ? error.code : VOICE_CODE.micUnavailable,
        retryable: false,
        message: error instanceof Error ? error.message : null,
        hasAudio: false,
      });
      return;
    }
    if (generation !== this.generation) {
      void recorder.stop().catch(() => undefined);
      return;
    }
    if (this.kindNow() === 'starting') this.publishRecording();
  }

  /** Stop recording and transcribe what was captured. */
  async stop(reason: InterruptReason | null = null): Promise<void> {
    if (this.state.kind === 'starting') {
      this.cancel();
      return;
    }
    if (this.state.kind !== 'recording') return;
    const generation = this.generation;
    this.set({ kind: 'transcribing', interruptedBy: reason, lagging: this.lagging });
    const recorder = this.recorder;
    this.recorder = null;
    try {
      await recorder?.stop();
    } catch {
      // The samples already buffered are what gets transcribed.
    }
    if (generation !== this.generation) return;
    this.uploader?.close();
  }

  /** Throw the recording away, on the agent too. */
  cancel(): void {
    this.generation += 1;
    this.discard();
    this.set({ kind: 'idle' });
  }

  /** Retry a failed upload from the buffered audio. */
  retry(): void {
    const state = this.state;
    if (state.kind !== 'failed' || !state.retryable || !state.hasAudio) return;
    if (this.uploader?.retry() === true) {
      this.lagging = false;
      this.set({ kind: 'transcribing', interruptedBy: null, lagging: false });
    }
  }

  /** Leave a result, an empty result or a failure. */
  dismiss(): void {
    const kind = this.state.kind;
    if (kind === 'result' || kind === 'empty' || kind === 'failed') this.cancel();
  }

  /** Leaving the session or switching agents. */
  dispose(): void {
    if (this.disposed) return;
    this.cancel();
    this.disposed = true;
  }

  /** Read through a call: the state changes across awaits and callbacks. */
  private kindNow(): DictationState['kind'] {
    return this.state.kind;
  }

  private isActive(): boolean {
    const kind = this.kindNow();
    return kind === 'starting' || kind === 'recording' || kind === 'transcribing' || kind === 'cleaning';
  }

  /** A cleanup that fails in any way leaves the transcript as heard. */
  private async cleanUp(generation: number, text: string, cleaner: TranscriptCleaner): Promise<void> {
    const controller = new AbortController();
    this.cleanupAbort = controller;
    const outcome = await runCleanup(cleaner, text, controller);
    if (this.cleanupAbort === controller) this.cleanupAbort = null;
    if (generation !== this.generation || this.kindNow() !== 'cleaning') return;
    switch (outcome.kind) {
      case 'cleaned':
        this.set({ kind: 'result', text: outcome.text, original: text });
        break;
      case 'unchanged':
        this.set({ kind: 'result', text });
        break;
      case 'failed':
        this.set({ kind: 'result', text, cleanupError: outcome.error });
        break;
    }
  }

  private async fail(failure: UploadFailure): Promise<void> {
    const generation = this.generation;
    const recorder = this.recorder;
    this.recorder = null;
    if (recorder !== null) {
      try {
        await recorder.stop();
      } catch {
        // Already failing.
      }
      if (generation !== this.generation) return;
    }
    this.set({
      kind: 'failed',
      code: failure.code,
      retryable: failure.retryable,
      message: failure.message,
      hasAudio: (this.buffer?.sampleCount ?? 0) > 0,
    });
  }

  private finishWith(state: DictationState): void {
    // The agent drops a finished dictation by itself; no DELETE needed.
    this.buffer?.release();
    this.buffer = null;
    this.uploader = null;
    this.set(state);
  }

  private discard(): void {
    this.cleanupAbort?.abort();
    this.cleanupAbort = null;
    const recorder = this.recorder;
    this.recorder = null;
    void recorder?.stop().catch(() => undefined);
    this.uploader?.cancel();
    this.uploader = null;
    this.buffer?.release();
    this.buffer = null;
    this.level = 0;
    this.lagging = false;
  }

  private publishProgress(): void {
    if (this.state.kind === 'recording') {
      this.publishRecording();
    } else if (this.state.kind === 'transcribing' && this.state.lagging !== this.lagging) {
      this.set({ ...this.state, lagging: this.lagging });
    }
  }

  private publishRecording(): void {
    const buffer = this.buffer;
    if (buffer === null) return;
    const { sampleRate } = this.options;
    const elapsedMs = Math.floor((buffer.sampleCount * 1000) / sampleRate);
    const left = (buffer.capacitySamples - buffer.sampleCount) / sampleRate;
    this.set({
      kind: 'recording',
      elapsedMs,
      level: this.level,
      remainingSeconds: left <= COUNTDOWN_SECONDS ? Math.max(0, Math.ceil(left)) : null,
      lagging: this.lagging,
    });
  }

  private set(state: DictationState): void {
    this.state = state;
    this.options.onChange(state);
  }
}

function cleanupError(error: unknown, timedOut: boolean): CleanupError {
  if (timedOut) return { code: VOICE_CODE.cleanupTimeout, message: null };
  if (isVoiceApiError(error)) {
    return {
      code: error.network ? VOICE_CODE.network : (error.code ?? VOICE_CODE.generic),
      message: error.detailMessage ?? error.message,
    };
  }
  return { code: VOICE_CODE.generic, message: error instanceof Error ? error.message : null };
}

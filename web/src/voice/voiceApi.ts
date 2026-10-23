/**
 * The dictation half of the agent's voice API, as the uploader sees it.
 * `api/voiceRepository.ts` implements it over the relay; tests script it.
 */
export interface VoiceDictationOpened {
  dictationId: string;
  sampleRate: number;
  maxSeconds: number;
  maxChunkBytes: number;
}

export interface VoiceFinishResult {
  /** `done` (text set) or `transcribing` (call `finish` again); open vocabulary. */
  state: string;
  text: string | null;
}

export interface VoiceApi {
  create(signal: AbortSignal): Promise<VoiceDictationOpened>;
  audio(dictationId: string, seq: number, pcm16B64: string, signal: AbortSignal): Promise<void>;
  finish(dictationId: string, lastSeq: number, signal: AbortSignal): Promise<VoiceFinishResult>;
  /** Idempotent abort; callers ignore every failure of it. */
  remove(dictationId: string): Promise<void>;
}

/**
 * A refused or failed voice request. `code` is the agent's `detail.code`
 * (an open vocabulary); `network` marks a request that never got an answer
 * (offline, timeout, connection reset).
 */
export class VoiceApiError extends Error {
  readonly status: number | null;
  readonly code: string | null;
  readonly detailMessage: string | null;
  readonly expectedSeq: number | null;
  readonly network: boolean;

  constructor(init: {
    status: number | null;
    code: string | null;
    message: string;
    detailMessage?: string | null;
    expectedSeq?: number | null;
    network?: boolean;
  }) {
    super(init.message);
    this.name = 'VoiceApiError';
    this.status = init.status;
    this.code = init.code;
    this.detailMessage = init.detailMessage ?? null;
    this.expectedSeq = init.expectedSeq ?? null;
    this.network = init.network ?? false;
  }
}

export function isVoiceApiError(error: unknown): error is VoiceApiError {
  return error instanceof VoiceApiError;
}

export const VOICE_CODE = {
  notReady: 'voice_not_ready',
  busy: 'voice_busy',
  unsupported: 'voice_unsupported',
  dictationNotFound: 'dictation_not_found',
  seqGap: 'seq_gap',
  dictationFinished: 'dictation_finished',
  tooLong: 'too_long',
  chunkTooLarge: 'chunk_too_large',
  badAudio: 'bad_audio',
  transcriptionFailed: 'transcription_failed',
  /** Client-side codes, never sent by the agent. */
  network: 'network',
  transcriptionTimeout: 'transcription_timeout',
  micPermissionDenied: 'mic_permission_denied',
  micUnavailable: 'mic_unavailable',
  generic: 'generic',
} as const;

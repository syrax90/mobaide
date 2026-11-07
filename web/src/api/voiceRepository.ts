/**
 * The agent's voice routes (`/api/voice/*`, PROTOCOL.md "on-PC voice input").
 *
 * The status answers are decoded through the Zod mirror in
 * `protocol/dto/voice.ts`, because the same body arrives as the
 * `voice.changed` event; the dictation bodies are REST-only and use the
 * generated types. Every refusal carries `{"detail": {"code", "message", …}}`
 * — `toApiError` reads `detail.error`, so the code is lifted here into a
 * `VoiceApiError`, which is what the uploader and the screens match on.
 */
import { createAgentClient } from './clients';
import { isApiError, toApiError } from './errors';
import type { components } from './generated/agent';
import { voiceStatusSchema, type VoiceStatus } from '../protocol/dto/voice';
import { VoiceApiError, type VoiceApi } from '../voice/voiceApi';

type DictationResponse = components['schemas']['VoiceDictationResponse'];
type DictationResult = components['schemas']['VoiceDictationResultResponse'];
type CleanupResult = components['schemas']['VoiceCleanupResponse'];

/** Turn any failure of a voice request into a `VoiceApiError`. */
export function toVoiceApiError(error: unknown): VoiceApiError {
  if (error instanceof VoiceApiError) return error;
  if (isApiError(error)) {
    let code: string | null = null;
    let detailMessage: string | null = null;
    let expectedSeq: number | null = null;
    if (typeof error.detail === 'object' && error.detail !== null) {
      const detail = error.detail as Record<string, unknown>;
      if (typeof detail.code === 'string') code = detail.code;
      if (typeof detail.message === 'string') detailMessage = detail.message;
      if (typeof detail.expected_seq === 'number') expectedSeq = detail.expected_seq;
    }
    return new VoiceApiError({
      status: error.status,
      code,
      message: error.message,
      detailMessage,
      expectedSeq,
    });
  }
  // fetch rejected: offline, aborted by a timeout, reset by the relay.
  return new VoiceApiError({
    status: null,
    code: null,
    message: error instanceof Error ? error.message : 'The request did not complete.',
    network: true,
  });
}

interface FetchResult<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

async function call<T>(request: () => Promise<FetchResult<T>>): Promise<T> {
  let result: FetchResult<T>;
  try {
    result = await request();
  } catch (error) {
    throw toVoiceApiError(error);
  }
  if (result.error !== undefined || result.data === undefined) {
    throw toVoiceApiError(toApiError(result.response, result.error));
  }
  return result.data;
}

function decodeStatus(body: unknown): VoiceStatus {
  const decoded = voiceStatusSchema.safeParse(body);
  if (!decoded.success) {
    throw new VoiceApiError({ status: null, code: null, message: 'Unreadable voice status.' });
  }
  return decoded.data;
}

export async function getVoiceStatus(agentId: string, signal?: AbortSignal): Promise<VoiceStatus> {
  const body = await call(() =>
    createAgentClient(agentId).GET('/api/voice/status', signal === undefined ? {} : { signal }),
  );
  return decodeStatus(body);
}

/** Enable voice and start (or retry) the install; answers at once. */
export async function installVoice(agentId: string): Promise<VoiceStatus> {
  const body = await call(() =>
    createAgentClient(agentId).POST('/api/voice/install', { body: {} }),
  );
  return decodeStatus(body);
}

/** Stop an install (while repairing, only the runtime download stops). */
export async function cancelVoiceInstall(agentId: string): Promise<VoiceStatus> {
  const body = await call(() => createAgentClient(agentId).POST('/api/voice/install/cancel'));
  return decodeStatus(body);
}

/** Turn voice off and free the disk. */
export async function removeVoice(agentId: string): Promise<VoiceStatus> {
  const body = await call(() => createAgentClient(agentId).DELETE('/api/voice/model'));
  return decodeStatus(body);
}

/** Turn transcript cleanup on or off for every client of this agent. */
export async function setVoiceCleanup(agentId: string, enabled: boolean): Promise<VoiceStatus> {
  const body = await call(() =>
    createAgentClient(agentId).PUT('/api/voice/cleanup/settings', { body: { enabled } }),
  );
  return decodeStatus(body);
}

/**
 * Tidies a transcript with Claude Code on the agent (fillers, self-corrections,
 * punctuation, technical terms). The agent answers within 20 s.
 */
export async function cleanupTranscript(
  agentId: string,
  text: string,
  signal: AbortSignal,
): Promise<string> {
  const body: CleanupResult = await call(() =>
    createAgentClient(agentId).POST('/api/voice/cleanup', { body: { text }, signal }),
  );
  return body.text;
}

/** The dictation routes of one agent, for the uploader. */
export function createVoiceApi(agentId: string, sessionId: string | null): VoiceApi {
  const client = createAgentClient(agentId);
  return {
    async create(signal) {
      const body: DictationResponse = await call(() =>
        client.POST('/api/voice/dictations', {
          body: sessionId === null ? {} : { session_id: sessionId },
          signal,
        }),
      );
      return {
        dictationId: body.dictation_id,
        sampleRate: body.sample_rate,
        maxSeconds: body.max_seconds,
        maxChunkBytes: body.max_chunk_bytes,
      };
    },
    async audio(dictationId, seq, pcm16B64, signal) {
      await call(() =>
        client.POST('/api/voice/dictations/{dictation_id}/audio', {
          params: { path: { dictation_id: dictationId } },
          body: { seq, pcm16_b64: pcm16B64 },
          signal,
        }),
      );
    },
    async finish(dictationId, lastSeq, signal) {
      const body: DictationResult = await call(() =>
        client.POST('/api/voice/dictations/{dictation_id}/finish', {
          params: { path: { dictation_id: dictationId } },
          body: { last_seq: lastSeq },
          signal,
        }),
      );
      return { state: body.state, text: body.text ?? null };
    },
    async remove(dictationId) {
      // 204 has no body, so openapi-fetch reports neither data nor error.
      const { response } = await client.DELETE('/api/voice/dictations/{dictation_id}', {
        params: { path: { dictation_id: dictationId } },
      });
      if (!response.ok && response.status !== 404) {
        throw new VoiceApiError({ status: response.status, code: null, message: 'Delete failed.' });
      }
    },
  };
}

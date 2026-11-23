/**
 * Cloud voice input (`/api/voice/cloud`, PROTOCOL.md "cloud voice input") —
 * central-server axis, authenticated with the user session.
 *
 * The dictation routes mirror the agent's `/api/voice/dictations` body for
 * body, so `createCloudVoiceApi` gives the uploader the same `VoiceApi` it
 * gets for an agent. Refusals use the same `{"detail": {"code", …}}` envelope
 * and become a `VoiceApiError`.
 */
import { relayClient } from './clients';
import { toApiError } from './errors';
import { callVoice } from './voiceRepository';
import {
  CLOUD_VOICE_STATE,
  cloudVoiceSchema,
  type CloudVoice,
  type VoiceEngine,
} from '../protocol/dto/cloudVoice';
import { VoiceApiError, type VoiceApi } from '../voice/voiceApi';

/** What a server without cloud voice input amounts to. */
const NOT_OFFERED: CloudVoice = {
  state: CLOUD_VOICE_STATE.off,
  limits: { sample_rate: 16000, max_seconds: 120, max_chunk_bytes: 65536 },
  usage: { used_seconds: 0, limit_seconds: 0, resets_at: '' },
  engines: {},
};

export async function getCloudVoice(signal?: AbortSignal): Promise<CloudVoice> {
  const { data, error, response } = await relayClient.GET(
    '/api/voice/cloud',
    signal === undefined ? {} : { signal },
  );
  // A server from before cloud voice input has no such route.
  if (response.status === 404) return NOT_OFFERED;
  if (error !== undefined || data === undefined) throw toApiError(response, error);
  return cloudVoiceSchema.parse(data);
}

/** Choose where one agent's dictations are transcribed; the server tells the other devices. */
export async function setVoiceEngine(agentId: string, engine: VoiceEngine): Promise<CloudVoice> {
  const body = await callVoice(() =>
    relayClient.PUT('/api/voice/cloud/engines/{agent_id}', {
      params: { path: { agent_id: agentId } },
      body: { engine },
    }),
  );
  return cloudVoiceSchema.parse(body);
}

/** The cloud dictation routes, for the uploader. */
export function createCloudVoiceApi(): VoiceApi {
  return {
    async create(signal) {
      const body = await callVoice(() => relayClient.POST('/api/voice/cloud/dictations', { signal }));
      return {
        dictationId: body.dictation_id,
        sampleRate: body.sample_rate,
        maxSeconds: body.max_seconds,
        maxChunkBytes: body.max_chunk_bytes,
      };
    },
    async audio(dictationId, seq, pcm16B64, signal) {
      await callVoice(() =>
        relayClient.POST('/api/voice/cloud/dictations/{dictation_id}/audio', {
          params: { path: { dictation_id: dictationId } },
          body: { seq, pcm16_b64: pcm16B64 },
          signal,
        }),
      );
    },
    async finish(dictationId, lastSeq, signal) {
      const body = await callVoice(() =>
        relayClient.POST('/api/voice/cloud/dictations/{dictation_id}/finish', {
          params: { path: { dictation_id: dictationId } },
          body: { last_seq: lastSeq },
          signal,
        }),
      );
      return { state: body.state, text: body.text ?? null };
    },
    async remove(dictationId) {
      const { response } = await relayClient.DELETE('/api/voice/cloud/dictations/{dictation_id}', {
        params: { path: { dictation_id: dictationId } },
      });
      if (!response.ok && response.status !== 404) {
        throw new VoiceApiError({ status: response.status, code: null, message: 'Delete failed.' });
      }
    },
  };
}

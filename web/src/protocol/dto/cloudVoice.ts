/**
 * `CloudVoiceStatus` — the body of `GET /api/voice/cloud` and of
 * `PUT /api/voice/cloud/engines/{agent_id}` (PROTOCOL.md, "cloud voice
 * input"). Pinned by `tests/protocol_fixtures/dto/central/CloudVoiceStatus.json`.
 *
 * `state` and the `engines` values are open vocabularies, kept as strings: a
 * state this client does not know reads as "not offered", and an engine it
 * does not know as "no choice yet".
 */
import { z } from 'zod';

export const cloudVoiceSchema = z.looseObject({
  state: z.string(),
  limits: z.looseObject({
    sample_rate: z.number(),
    max_seconds: z.number(),
    max_chunk_bytes: z.number(),
  }),
  usage: z.looseObject({
    used_seconds: z.number(),
    limit_seconds: z.number(),
    resets_at: z.string(),
  }),
  engines: z.record(z.string(), z.string()),
});

export type CloudVoice = z.infer<typeof cloudVoiceSchema>;

/** The payload of the `cloud_voice.changed` user event. */
export const cloudVoiceChangedSchema = z.looseObject({
  engines: z.record(z.string(), z.string()),
});

export const CLOUD_VOICE_STATE = {
  off: 'off',
  ready: 'ready',
  unavailable: 'unavailable',
  limitReached: 'limit_reached',
} as const;

/** Where one agent's dictations are transcribed. */
export const VOICE_ENGINE = {
  cloud: 'cloud',
  local: 'local',
} as const;

export type VoiceEngine = (typeof VOICE_ENGINE)[keyof typeof VOICE_ENGINE];

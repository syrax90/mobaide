/**
 * `VoiceStatusResponse` — the body of `GET /api/voice/status` and of the
 * install/cancel/remove answers, and the payload of the `voice.changed` agent
 * event. Pinned by `tests/protocol_fixtures/dto/agent/VoiceStatusResponse.json`.
 *
 * REST answers could use the generated type, but the event arrives untyped on
 * the socket, so this Zod mirror is the one decoder for both. `state`,
 * `host.recommendation`, `reasons[].code` and `error.code` are open
 * vocabularies, kept as strings: a value this client does not know reads as
 * "voice unavailable", never as a parse failure.
 */
import { z } from 'zod';
import { optionalWireBoolean, optionalWireNumber, optionalWireString } from '../wireSchema';

const voiceModelSchema = z.looseObject({
  id: z.string(),
  name: z.string(),
  languages: z.array(z.string()),
  download_bytes: z.number(),
  disk_bytes: z.number(),
  ram_bytes: z.number(),
  notice: optionalWireString(),
});

const voiceProgressSchema = z.looseObject({
  done_bytes: z.number(),
  total_bytes: z.number(),
});

const voiceReasonSchema = z.looseObject({
  code: z.string(),
  message: z.string(),
});

const voiceHostSchema = z.looseObject({
  ram_total_bytes: optionalWireNumber(),
  disk_free_bytes: optionalWireNumber(),
  cpu_count: optionalWireNumber(),
  recommendation: z.string(),
  reasons: z.array(voiceReasonSchema),
});

export const voiceStatusSchema = z.looseObject({
  state: z.string(),
  enabled: z.boolean(),
  repairing: optionalWireBoolean(),
  model: voiceModelSchema,
  progress: voiceProgressSchema.nullish().transform((value) => value ?? undefined),
  host: voiceHostSchema,
  speed_factor: optionalWireNumber(),
  error: voiceReasonSchema.nullish().transform((value) => value ?? undefined),
  /** The agent's transcript-cleanup choice, shared by every client. */
  cleanup_enabled: optionalWireBoolean(),
});

export type VoiceStatus = z.infer<typeof voiceStatusSchema>;

export const VOICE_STATE = {
  unsupported: 'unsupported',
  notInstalled: 'not_installed',
  queued: 'queued',
  downloading: 'downloading',
  verifying: 'verifying',
  ready: 'ready',
  failed: 'failed',
} as const;

export const VOICE_RECOMMENDATION_LOW = 'low_resources';

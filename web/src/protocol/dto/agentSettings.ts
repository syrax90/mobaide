/**
 * `AgentSettingsResponse` — the body of `GET /api/agent/settings` and of
 * every settings write, and the payload of the `agent_settings.changed` agent
 * event. Pinned by `tests/protocol_fixtures/dto/agent/AgentSettingsResponse.json`.
 *
 * The event arrives untyped on the socket, so this Zod mirror is the one
 * decoder for both. `keep_awake.state` is an open vocabulary: a value this
 * client does not know reads as "not active", never as a parse failure.
 */
import { z } from 'zod';
import { optionalWireString } from '../wireSchema';

export const keepAwakeStatusSchema = z.looseObject({
  enabled: z.boolean(),
  state: z.string(),
  message: optionalWireString(),
});

export const agentSettingsSchema = z.looseObject({
  keep_awake: keepAwakeStatusSchema,
});

export type KeepAwakeStatus = z.infer<typeof keepAwakeStatusSchema>;
export type AgentSettings = z.infer<typeof agentSettingsSchema>;

export const KEEP_AWAKE_STATE = {
  off: 'off',
  active: 'active',
  failed: 'failed',
  unsupported: 'unsupported',
} as const;

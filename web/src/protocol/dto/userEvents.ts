/**
 * Payload shapes carried by the user-scoped events channel (`ws/user-events`).
 *
 * Pinned by `tests/protocol_fixtures/dto/central/{TemplateDto,
 * TemplateDeletedPayload,ShortcutDto,ShortcutDeletedPayload,
 * CustomAgentTypeDto,CustomTypeDeletedPayload}.json`. `agent.status_changed`
 * and `session.attention` have no fixture file; their shapes come from
 * PROTOCOL.md and the Android/iOS payload structs.
 */
import { z } from 'zod';
import { optionalWireString } from '../wireSchema';

export const agentStatusChangedSchema = z.looseObject({
  agent_id: z.string(),
  online: z.boolean(),
});

export const templateSchema = z.looseObject({
  template_id: z.string(),
  name: z.string(),
  body: z.string(),
  is_favorite: z.boolean(),
  last_used_at: optionalWireString(),
  created_at: z.string(),
  updated_at: z.string(),
  version: z.number(),
});

export const templateDeletedSchema = z.looseObject({
  template_id: z.string(),
  version: z.number(),
  updated_at: z.string(),
});

export const shortcutSchema = z.looseObject({
  shortcut_id: z.string(),
  /**
   * The session-kind wire string, stored opaquely by the server. Not
   * `openSessionKind()`: this field is never null on the wire, and coercing it
   * would break the `unknown_kind_preserved` round trip.
   */
  kind: z.string(),
  chord_token: z.string(),
  name: optionalWireString(),
  position: z.number(),
  created_at: z.string(),
  updated_at: z.string(),
  version: z.number(),
});

export const shortcutDeletedSchema = z.looseObject({
  shortcut_id: z.string(),
  version: z.number(),
  updated_at: z.string(),
});

export const customAgentTypeSchema = z.looseObject({
  /** 12 lowercase hex chars; the session kind is `custom_` + this id. */
  id: z.string(),
  name: z.string(),
  command: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
});

/** Tombstone for `custom_type.deleted` — the key is `id`, not `type_id`. */
export const customTypeDeletedSchema = z.looseObject({
  id: z.string(),
});

/**
 * Slim attention notice (proto-v6 addition), not the full session wire dict.
 * `agent_id` is stamped by the server from the authenticated tunnel.
 */
export const sessionAttentionSchema = z.looseObject({
  session_id: z.string(),
  reason: z.string(),
  title: optionalWireString(),
  project_name: optionalWireString(),
  exit_reason: optionalWireString(),
  agent_id: optionalWireString(),
});

export type Template = z.infer<typeof templateSchema>;
export type Shortcut = z.infer<typeof shortcutSchema>;
export type CustomAgentType = z.infer<typeof customAgentTypeSchema>;
export type AgentStatusChanged = z.infer<typeof agentStatusChangedSchema>;
export type SessionAttention = z.infer<typeof sessionAttentionSchema>;

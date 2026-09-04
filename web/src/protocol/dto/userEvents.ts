/**
 * Payload shapes carried by the user-scoped events channel (`ws/user-events`).
 *
 * Pinned by `tests/protocol_fixtures/dto/central/{TemplateDto,
 * TemplateDeletedPayload,CustomAgentTypeDto,CustomTypeDeletedPayload,
 * FirstAgentPairedPayload}.json`.
 * `agent.status_changed` and `session.attention` have no fixture file; their
 * shapes come from PROTOCOL.md and the Android/iOS payload structs.
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

/**
 * Sent once per account, when the relay stamps `users.first_paired_at` on the
 * first-ever pairing. The mobile apps log their Firebase `agent_paired`
 * conversion off it; the web client only refetches the agent list.
 */
export const firstAgentPairedSchema = z.looseObject({
  agent_id: z.string(),
});

export type Template = z.infer<typeof templateSchema>;
export type CustomAgentType = z.infer<typeof customAgentTypeSchema>;
export type AgentStatusChanged = z.infer<typeof agentStatusChangedSchema>;
export type SessionAttention = z.infer<typeof sessionAttentionSchema>;
export type FirstAgentPaired = z.infer<typeof firstAgentPairedSchema>;

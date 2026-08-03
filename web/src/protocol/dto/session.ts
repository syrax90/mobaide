/**
 * Agent session wire shapes, pinned by
 * `tests/protocol_fixtures/dto/agent/{SessionKind,SessionResponse}.json`.
 */
import { z } from 'zod';
import { openSessionKind, optionalWireNumber, optionalWireString } from '../wireSchema';

/**
 * `kind` is an open string since proto-v7 — `claude` / `codex` / `antigravity` /
 * `gemini` / `auto_scan` / `shell` / `custom_<12hex>` / anything a newer server
 * invents. Never narrowed to a union: coercing an unknown kind would silently
 * mislabel a session.
 */
export const sessionKindSchema = z.string();

/**
 * The single-session wire dict (`session_to_wire_dict`), carried by the
 * `sessions.changed` / `session.updated` / `session.attention` envelopes and by
 * the REST session list.
 *
 * `command` materialises to `[]` and `kind` to `claude` when absent, matching
 * the agent-side Pydantic defaults the golden fixtures assert.
 */
export const sessionResponseSchema = z.looseObject({
  session_id: z.string(),
  state: z.string(),
  created_at: z.string(),
  pid: optionalWireNumber(),
  command: z.array(z.string()).default([]),
  cwd: optionalWireString(),
  title: optionalWireString(),
  project_name: optionalWireString(),
  kind: openSessionKind(),
  /** Display name snapshotted at creation; null for built-in kinds. */
  kind_label: optionalWireString(),
  attention: optionalWireString(),
  exit_reason: optionalWireString(),
});

export type SessionResponse = z.infer<typeof sessionResponseSchema>;

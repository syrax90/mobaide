/**
 * Agent session wire shapes, pinned by
 * `tests/protocol_fixtures/dto/agent/{SessionKind,SessionResponse}.json`.
 */
import { z } from 'zod';
import { openSessionKind, optionalWireNumber, optionalWireString } from '../wireSchema';

/**
 * `kind` is an open string since proto-v7 — `claude` / `codex` / `antigravity` /
 * `opencode` / `gemini` / `auto_scan` / `shell` / `custom_<12hex>` / anything a newer server
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
  /** `chat` for a structured chat session; absent or anything else is the terminal. */
  ui: optionalWireString(),
  /**
   * `working` while the agent is mid-turn, `waiting` while it is parked on
   * background work, `idle` between turns; absent when unknown.
   */
  activity: optionalWireString(),
});

export type SessionResponse = z.infer<typeof sessionResponseSchema>;

export function isChatSession(session: Pick<SessionResponse, 'ui'>): boolean {
  return session.ui === 'chat';
}

/**
 * `sessions.changed` — the agent's complete session list, rebuilt from
 * `list_sessions()` on every create, stop and state change, and pushed again on
 * every (re)connect (`core/session_manager.py::_publish_sessions_changed`).
 * Authoritative: ids missing from it no longer exist.
 */
export const sessionsChangedSchema = z.looseObject({
  sessions: z.array(sessionResponseSchema),
});

/**
 * `session.updated` and the agent-channel `session.attention` — one **whole**
 * session row, not a partial patch (`agent/ws/event_fanout.py`). That is what
 * makes an upsert into the cached list safe.
 *
 * The user-events channel carries a differently-shaped, slimmer
 * `session.attention` (`{session_id, reason, agent_id, …}`); it is handled by
 * `userEvents.ts` and must not be decoded with this schema.
 */
export const sessionEventSchema = z.looseObject({
  session: sessionResponseSchema,
});

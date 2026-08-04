/**
 * The agent's session REST surface.
 *
 * Unlike `templateRepository` / `deviceRepository`, this does **not** map the
 * wire into a camelCase view type. The session list has two producers — this
 * endpoint and the `sessions.changed` / `session.updated` socket events — and
 * they write into the same query cache, so they have to agree byte for byte.
 * Running both through `sessionResponseSchema` is what guarantees that: the same
 * defaults materialise (`command: []`, `kind: 'claude'`), and the unknown keys
 * `z.looseObject` preserves survive on both paths.
 *
 * That is not a hand-written wire interface of the kind the camelCase rule
 * exists to prevent — the schema is pinned by the shared golden
 * `tests/protocol_fixtures/dto/agent/SessionResponse.json`, which is a stronger
 * gate than the generated types, and is what both mobile clients decode with.
 */
import { createAgentClient } from './clients';
import { toApiError } from './errors';
import { sessionResponseSchema, type SessionResponse } from '../protocol/dto/session';
import { createSessionRequestSchema } from '../protocol/dto/sessionCreate';
import type { components } from './generated/agent';

export type CreateSessionRequest = components['schemas']['CreateSessionRequest'];

export async function listSessions(agentId: string): Promise<SessionResponse[]> {
  const { data, error, response } = await createAgentClient(agentId).GET('/api/sessions');
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return data.map((session) => sessionResponseSchema.parse(session));
}

/**
 * Launch a session.
 *
 * The body is re-parsed through `createSessionRequestSchema` rather than sent as
 * assembled: that is what turns "always send every boolean explicitly" from a
 * convention into something the strict schema enforces, and it is the last line
 * of defence against a field leaking in from a branch the user left.
 */
export async function createSession(
  agentId: string,
  request: CreateSessionRequest,
): Promise<SessionResponse> {
  const body = createSessionRequestSchema.parse(request) as CreateSessionRequest;
  const { data, error, response } = await createAgentClient(agentId).POST('/api/sessions', {
    body,
  });
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return sessionResponseSchema.parse(data);
}

/**
 * Stop a session. `force` maps to the agent's SIGKILL path; the list offers both
 * because iOS does, and an unconfirmed kill on a desktop-sized target is worse
 * than on a phone.
 */
export async function stopSession(
  agentId: string,
  sessionId: string,
  force: boolean,
): Promise<void> {
  const { error, response } = await createAgentClient(agentId).DELETE(
    '/api/sessions/{session_id}',
    { params: { path: { session_id: sessionId }, query: { force } } },
  );
  if (error !== undefined) throw toApiError(response, error);
}

/**
 * `GET /api/chat/{kind}/sessions` — the sessions a chat agent stored on the PC,
 * pinned by `tests/protocol_fixtures/dto/agent/ChatStoredSessionsResponse.json`.
 */
import { z } from 'zod';
import { optionalWireString } from '../wireSchema';

export const chatStoredSessionSchema = z.looseObject({
  session_id: z.string(),
  cwd: z.string(),
  title: optionalWireString(),
  updated_at: optionalWireString(),
  /** The open Mobaide session that already shows this row: open it, never a copy. */
  open_session_id: optionalWireString(),
});

export const chatStoredSessionsResponseSchema = z.looseObject({
  sessions: z.array(chatStoredSessionSchema),
  next_cursor: optionalWireString(),
});

export type ChatStoredSession = z.infer<typeof chatStoredSessionSchema>;
export type ChatStoredSessionsResponse = z.infer<typeof chatStoredSessionsResponseSchema>;

/**
 * The `{ "type": <kind>, "payload": {...} }` envelope shared by the agent-events
 * (`ws/events`) and user-events (`ws/user-events`) channels.
 *
 * Drop rules, matching iOS `AgentEvent.swift` and Android
 * `AgentEventBus.parseAndEmit`: a malformed outer envelope and a payload that
 * fails its type-specific schema are both dropped silently. Only a well-formed
 * envelope with an unrecognised `type` is surfaced — as "unknown", so callers
 * can count it without acting on it.
 *
 * `payload` must be *present*; the key being absent drops the message. Its
 * contents stay `unknown` here because the shape is chosen by `type`.
 */
import { z } from 'zod';
import { parseWireJson } from '../wireJson';

export const eventEnvelopeSchema = z.looseObject({
  type: z.string(),
  payload: z.unknown(),
});

export interface EventEnvelope {
  type: string;
  payload: unknown;
}

/**
 * Parse one text frame into an envelope, or `null` if it is not one.
 *
 * Note the explicit `'payload' in parsed` check: `z.unknown()` accepts a
 * missing key, and both mobile clients require the key to be present.
 */
export function decodeEventEnvelope(text: string): EventEnvelope | null {
  let parsed: unknown;
  try {
    parsed = parseWireJson(text);
  } catch {
    return null;
  }
  if (typeof parsed !== 'object' || parsed === null || !('payload' in parsed)) {
    return null;
  }
  const result = eventEnvelopeSchema.safeParse(parsed);
  if (!result.success) return null;
  return { type: result.data.type, payload: result.data.payload };
}

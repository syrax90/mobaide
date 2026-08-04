/**
 * Outbound custom-agent-type wire shapes, pinned by
 * `tests/protocol_fixtures/dto/central/CreateCustomAgentTypeRequest.json`.
 *
 * `z.strictObject` for the same reason as `./sessionCreate`: these are requests,
 * so an unexpected key is a bug on this side rather than a newer server.
 */
import { z } from 'zod';

/** `central_server/custom_agent_types/models.py::MAX_TYPE_NAME`. */
export const MAX_TYPE_NAME_LENGTH = 60;
/** `central_server/custom_agent_types/models.py::MAX_TYPE_COMMAND`. */
export const MAX_TYPE_COMMAND_LENGTH = 1000;

/**
 * Server-side these limits are Pydantic `max_length`, which counts **code
 * points**. `String.length` counts UTF-16 units, so it double-counts astral
 * characters and would reject a name the server accepts. Android uses
 * `codePointCount` and iOS `unicodeScalars.count` for the same reason.
 */
export function codePointLength(value: string): number {
  return [...value].length;
}

const typeText = (max: number) =>
  z
    .string()
    .transform((value) => value.trim())
    .refine((value) => value !== '', { error: 'must not be blank' })
    .refine((value) => !value.includes('\u0000'), { error: 'must not contain NUL characters' })
    .refine((value) => codePointLength(value) <= max, {
      error: `must be at most ${String(max)} characters`,
    });

export const createCustomAgentTypeRequestSchema = z.strictObject({
  name: typeText(MAX_TYPE_NAME_LENGTH),
  command: typeText(MAX_TYPE_COMMAND_LENGTH),
});

export type CreateCustomAgentTypeRequestWire = z.infer<
  typeof createCustomAgentTypeRequestSchema
>;

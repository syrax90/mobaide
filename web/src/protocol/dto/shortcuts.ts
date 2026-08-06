/**
 * Outbound saved-shortcut wire shapes, pinned by
 * `tests/protocol_fixtures/dto/central/{CreateShortcutRequest,UpdateShortcutRequest}.json`.
 *
 * `z.strictObject` for the same reason as `./customAgentTypes`: these are
 * requests, so an unexpected key is a bug on this side rather than a newer
 * server. The inbound `ShortcutSummary` shape lives in `./userEvents` — it also
 * arrives over `ws/user-events`, which is why it is a `looseObject` there.
 */
import { z } from 'zod';
import { codePointLength } from './customAgentTypes';

/** `central_server/shortcuts/models.py::MAX_SHORTCUT_NAME`. */
export const MAX_SHORTCUT_NAME_LENGTH = 100;
/** `central_server/shortcuts/models.py::KIND_PATTERN`. */
export const SHORTCUT_KIND_PATTERN = /^[a-z0-9_]{1,32}$/;
/** `chord_token` is `Field(min_length=1, max_length=64)` — stored opaquely. */
export const MAX_CHORD_TOKEN_LENGTH = 64;

/**
 * Clamp a display name in the server's own unit.
 *
 * Pydantic's `max_length` counts **code points**; `String.prototype.slice`
 * counts UTF-16 units, so a naive slice of a name made of astral characters
 * still 422s. iOS `ShortcutPickerLogic.clampName` carries the same note.
 */
export function clampShortcutName(name: string): string {
  return [...name].slice(0, MAX_SHORTCUT_NAME_LENGTH).join('');
}

const shortcutName = z
  .string()
  .refine((value) => codePointLength(value) <= MAX_SHORTCUT_NAME_LENGTH, {
    error: `must be at most ${String(MAX_SHORTCUT_NAME_LENGTH)} characters`,
  });

export const createShortcutRequestSchema = z.strictObject({
  kind: z.string().regex(SHORTCUT_KIND_PATTERN, {
    error: 'must be a session-kind wire name',
  }),
  chord_token: z.string().min(1).max(MAX_CHORD_TOKEN_LENGTH),
  /**
   * Optional and nullable: the server treats an absent, null or empty name as
   * "no name", and a re-save with no name is a deliberate no-op that broadcasts
   * nothing, so a stale cache cannot wipe a name another device just set.
   */
  name: shortcutName.nullish(),
});

/**
 * Rename-only update. `name` is **required** — an empty body is a 422, so the
 * absent form cannot express "leave it alone" — and `""` is the wire sentinel
 * for clearing it back to NULL. Never `null`: Android's JSON encoder omits
 * null-valued defaults, so the empty string is the only clear form all three
 * clients can express.
 */
export const updateShortcutRequestSchema = z.strictObject({
  name: shortcutName,
});

export type CreateShortcutRequestWire = z.infer<typeof createShortcutRequestSchema>;
export type UpdateShortcutRequestWire = z.infer<typeof updateShortcutRequestSchema>;

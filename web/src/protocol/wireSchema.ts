/**
 * Shared policy for hand-written wire schemas.
 *
 * Three rules, all of them mirroring the mobile decoders
 * (`Json { ignoreUnknownKeys = true; coerceInputValues = true }` on Android,
 * stock `JSONDecoder` + explicit defaults on iOS):
 *
 * 1. **Wire objects use `z.looseObject`, never `z.object`.** A bare `z.object`
 *    strips unknown keys, which would quietly discard fields a newer server
 *    added — the opposite of the forward-compatibility the protocol promises.
 * 2. **Kind strings are open.** Since proto-v7 a session `kind` may be any
 *    string (`custom_<12hex>` today, anything tomorrow) and must be preserved
 *    verbatim rather than coerced to a known member. Only an explicit `null`
 *    falls back to the default.
 * 3. **Absent and explicitly-null optionals both decode to `undefined`**, so
 *    re-encoding drops the key. That is what makes the `decoded_canonical`
 *    round trips in `tests/protocol_fixtures/dto/**` agree with iOS, whose
 *    encoder omits nil.
 */
import { z } from 'zod';

/** The kind an explicit `null` falls back to (agent-side default). */
export const DEFAULT_SESSION_KIND = 'claude';

/** `custom_<12 lowercase hex>` — the proto-v7 custom agent type kind. */
const CUSTOM_KIND_PATTERN = /^custom_[0-9a-f]{12}$/;

export function isCustomSessionKind(kind: string): boolean {
  return CUSTOM_KIND_PATTERN.test(kind);
}

/** The registry row id embedded in a `custom_*` kind, or null for other kinds. */
export function customSessionTypeId(kind: string): string | null {
  return isCustomSessionKind(kind) ? kind.slice('custom_'.length) : null;
}

/** An optional wire string: absent or explicitly null both become `undefined`. */
export function optionalWireString() {
  return z
    .string()
    .nullish()
    .transform((value) => value ?? undefined);
}

/** An optional wire number: absent or explicitly null both become `undefined`. */
export function optionalWireNumber() {
  return z
    .number()
    .nullish()
    .transform((value) => value ?? undefined);
}

/** An optional wire boolean: absent or explicitly null both become `undefined`. */
export function optionalWireBoolean() {
  return z
    .boolean()
    .nullish()
    .transform((value) => value ?? undefined);
}

/**
 * An open session-kind field. Unknown strings survive verbatim; only an
 * explicit `null` (or an absent key) becomes the default.
 */
export function openSessionKind() {
  return z
    .string()
    .nullish()
    .transform((value) => value ?? DEFAULT_SESSION_KIND);
}

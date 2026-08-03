/**
 * JSON parse/stringify for wire payloads, without silently rounding integers.
 *
 * `JSON.parse` collapses any integer above 2^53 into the nearest double, which
 * would corrupt the auto-scan `seq` cursor the client echoes back in
 * `resume_from_seq` — the mobile clients type it `Long`/`Int64` and iOS
 * documents it as "can exceed 2^53". In practice the agent's counter starts at
 * 0 and increments by one per event, so the hazard is theoretical; the guard is
 * cheap enough to keep anyway, and it costs no dependency.
 *
 * `parseWireJson` uses the ES2026 `JSON.parse` source-access reviver
 * (json-parse-with-source, shipped with `JSON.rawJSON` in V8 12.4 / Node 22) to
 * promote out-of-range integer literals to `bigint`. Runtimes without it fall
 * back to plain `JSON.parse`, which is lossless for every value below 2^53.
 *
 * `stringifyWire` is the inverse: a `bigint` is emitted as a bare JSON integer,
 * never a string and never an exponent — the server does `int(v)` on
 * `resume_from_seq` values.
 */

interface JsonSourceAccess {
  rawJSON?: (text: string) => unknown;
}

interface ParseSourceContext {
  source?: string;
}

const jsonHost = JSON as JsonSourceAccess;

/** True when this runtime exposes the reviver's `context.source`. */
export const WIRE_JSON_SOURCE_ACCESS = typeof jsonHost.rawJSON === 'function';

const INTEGER_LITERAL = /^-?\d+$/;

type SourceReviver = (
  key: string,
  value: unknown,
  context?: ParseSourceContext,
) => unknown;

const promoteUnsafeIntegers: SourceReviver = (_key, value, context) => {
  if (
    typeof value === 'number' &&
    !Number.isSafeInteger(value) &&
    typeof context?.source === 'string' &&
    INTEGER_LITERAL.test(context.source)
  ) {
    return BigInt(context.source);
  }
  return value;
};

/** Parse a wire frame. Throws `SyntaxError` on malformed JSON, like `JSON.parse`. */
export function parseWireJson(text: string): unknown {
  if (!WIRE_JSON_SOURCE_ACCESS) {
    return JSON.parse(text) as unknown;
  }
  return JSON.parse(text, promoteUnsafeIntegers) as unknown;
}

/** Serialise a wire frame, emitting `bigint` as a bare JSON integer. */
export function stringifyWire(value: unknown): string {
  return JSON.stringify(value, (_key, item: unknown) => {
    if (typeof item !== 'bigint') return item;
    const digits = item.toString();
    // Without source access there is no way to emit an out-of-range integer
    // literally, so fall back to the double. Only values above 2^53 lose
    // anything, and only on runtimes that could not have parsed them exactly
    // in the first place.
    return jsonHost.rawJSON !== undefined ? jsonHost.rawJSON(digits) : Number(digits);
  });
}

/**
 * Coerce a wire integer to `bigint`.
 *
 * Accepts the `number` a small `seq` parses to and the `bigint`
 * `parseWireJson` promotes a large one to, so ordering comparisons downstream
 * are exact regardless of magnitude. Anything else (missing, null, a float, a
 * string) yields `0n` — the same "absent means zero" rule Android
 * (`?: 0L`) and iOS (`?? 0`) apply.
 */
export function toWireSeq(value: unknown): bigint {
  if (typeof value === 'bigint') return value;
  if (typeof value === 'number' && Number.isFinite(value)) {
    return BigInt(Math.trunc(value));
  }
  return 0n;
}

/**
 * One `default_flags` row in the project form.
 *
 * The wire value is heterogeneous (`str | int | float | bool`) and reaches the
 * CLI verbatim, so the type is part of what the user edits rather than something
 * inferred at save time — the same three-way editor Android's `FlagEntry` and
 * iOS's `FlagEntry` present.
 */
import type { FlagValue } from '../../protocol/dto/projects';

export type FlagKind = 'string' | 'boolean' | 'number';

export interface FlagEntry {
  /** Stable across re-renders and reorders; never derived from `key`, which the user edits. */
  id: string;
  key: string;
  kind: FlagKind;
  value: string;
}

export const FLAG_KIND_LABELS: Record<FlagKind, string> = {
  string: 'String',
  boolean: 'Boolean',
  number: 'Number',
};

let nextFlagId = 0;

export function newFlagEntry(): FlagEntry {
  nextFlagId += 1;
  return { id: `flag-${String(nextFlagId)}`, key: '', kind: 'string', value: '' };
}

/**
 * The editor's (kind, text) rendering of a wire value — the one place the
 * branch order and the `true`/`false` spelling live, because `flagEntryFrom`
 * and `wireFlagsSignature` must agree exactly or every save resends the map.
 *
 * The ordering is the gotcha both mobile clients carry: a JSON **string**
 * `"true"` must stay a string. Android's `JsonPrimitive.booleanOrNull` inspects
 * the primitive's *content* and would return `true` for it; the same trap exists
 * in any "does this look boolean" check, so the string branch is tested first.
 */
function flagKindAndText(value: FlagValue): { kind: FlagKind; text: string } {
  if (typeof value === 'string') return { kind: 'string', text: value };
  if (typeof value === 'boolean') {
    return { kind: 'boolean', text: value ? 'true' : 'false' };
  }
  return { kind: 'number', text: String(value) };
}

/** Key order for a seeded editor; `wireFlagsSignature` must match it exactly. */
function sortedFlagKeys(flags: Readonly<Record<string, FlagValue>>): string[] {
  return Object.keys(flags).sort((a, b) => a.localeCompare(b));
}

/** Seed one editor row from a wire value. */
export function flagEntryFrom(key: string, value: FlagValue): FlagEntry {
  nextFlagId += 1;
  const { kind, text } = flagKindAndText(value);
  return { id: `flag-${String(nextFlagId)}`, key, kind, value: text };
}

/** Editor rows for a whole flag map, key-sorted so the form is stable. */
export function flagEntriesFrom(
  flags: Readonly<Record<string, FlagValue>>,
): FlagEntry[] {
  return sortedFlagKeys(flags).map((key) => flagEntryFrom(key, flags[key] as FlagValue));
}

/** The wire value one editor row encodes to. */
export function flagWireValue(entry: FlagEntry): FlagValue {
  switch (entry.kind) {
    case 'string':
      return entry.value;
    case 'boolean':
      return entry.value === 'true';
    case 'number': {
      // The same parse `flagValueError` validates with, deliberately. Mobile
      // prefers an integer then a double because Kotlin and Swift are typed;
      // in JS there is one number type and `JSON.stringify` emits an integral
      // value without a fractional part anyway, so the distinction is a no-op
      // on the wire. Splitting the parse is not: `parseFloat` accepts prefixes
      // `Number` rejects, so a row could validate and then ship something else.
      const parsed = Number(entry.value);
      return Number.isFinite(parsed) ? parsed : 0;
    }
  }
}

export function flagsToWire(
  entries: readonly FlagEntry[],
): Record<string, FlagValue> {
  const map: Record<string, FlagValue> = {};
  for (const entry of entries) {
    // `Object.defineProperty`, not assignment: `key` is user input, and a row
    // keyed `__proto__` would otherwise mutate the prototype instead of the map.
    Object.defineProperty(map, entry.key, {
      value: flagWireValue(entry),
      enumerable: true,
      writable: true,
      configurable: true,
    });
  }
  return map;
}

/**
 * Separators, so `{ ab: 'c' }` and `{ a: 'bc' }` cannot render alike. Spelled as
 * escapes rather than literal control bytes, which are invisible in an editor:
 * the two signature functions below are only useful while they agree exactly,
 * and a silent divergence makes every save resend `default_flags`.
 */
const FIELD_SEPARATOR = '\u0001';
const ROW_SEPARATOR = '\u0002';

function signatureRow(key: string, kind: FlagKind, value: string): string {
  return `${key}${FIELD_SEPARATOR}${kind}${FIELD_SEPARATOR}${value}`;
}

/** A comparable rendering of the editor, for "has the user changed anything". */
export function flagsSignature(entries: readonly FlagEntry[]): string {
  return entries
    .map((entry) => signatureRow(entry.key, entry.kind, entry.value))
    .join(ROW_SEPARATOR);
}

/**
 * The same signature for a wire map, so a saved project can be compared to the
 * editor without round-tripping it through `flagEntryFrom` — which would burn a
 * fresh row id on every keystroke-driven diff.
 *
 * Key-sorted to match `flagEntriesFrom`, which is how a seeded editor is
 * ordered. Once the user adds or reorders a row the two legitimately differ,
 * which is exactly the "dirty" answer wanted.
 */
export function wireFlagsSignature(flags: Readonly<Record<string, FlagValue>>): string {
  return sortedFlagKeys(flags)
    .map((key) => {
      const { kind, text } = flagKindAndText(flags[key] as FlagValue);
      return signatureRow(key, kind, text);
    })
    .join(ROW_SEPARATOR);
}

export function flagKeyError(entries: readonly FlagEntry[], index: number): string | null {
  const entry = entries[index];
  if (entry === undefined) return null;
  if (entry.key.trim() === '') return 'Key required';
  // The *second* occurrence is the one flagged, matching both mobile forms.
  if (entries.slice(0, index).some((other) => other.key === entry.key)) return 'Duplicate key';
  return null;
}

export function flagValueError(entry: FlagEntry): string | null {
  switch (entry.kind) {
    case 'string':
      return null;
    case 'boolean':
      return entry.value === 'true' || entry.value === 'false' ? null : 'Expected true/false';
    case 'number':
      return Number.isFinite(Number(entry.value)) && entry.value.trim() !== ''
        ? null
        : 'Not a number';
  }
}

export function flagsValid(entries: readonly FlagEntry[]): boolean {
  return entries.every(
    (entry, index) => flagKeyError(entries, index) === null && flagValueError(entry) === null,
  );
}

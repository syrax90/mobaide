import { describe, expect, it } from 'vitest';
import {
  flagEntriesFrom,
  flagEntryFrom,
  flagKeyError,
  flagValueError,
  flagWireValue,
  flagsSignature,
  flagsToWire,
  flagsValid,
  newFlagEntry,
  wireFlagsSignature,
  type FlagEntry,
  type FlagKind,
} from './flagEntry';

const entry = (key: string, kind: FlagKind, value: string): FlagEntry => ({
  ...newFlagEntry(),
  key,
  kind,
  value,
});

describe('flagEntryFrom', () => {
  it('keeps a JSON string "true" a string rather than reading it as a boolean', () => {
    // The trap both mobile decoders carry: a content-sniffing boolean check
    // turns `--flag "true"` into `--flag` with a boolean, changing the CLI call.
    expect(flagEntryFrom('mode', 'true')).toMatchObject({ kind: 'string', value: 'true' });
  });

  it('maps each wire type to its editor row', () => {
    expect(flagEntryFrom('plan', true)).toMatchObject({ kind: 'boolean', value: 'true' });
    expect(flagEntryFrom('plan', false)).toMatchObject({ kind: 'boolean', value: 'false' });
    expect(flagEntryFrom('effort', 3)).toMatchObject({ kind: 'number', value: '3' });
    expect(flagEntryFrom('temp', 1.5)).toMatchObject({ kind: 'number', value: '1.5' });
  });

  it('gives every row a distinct id, so a duplicated key still edits one row', () => {
    const rows = flagEntriesFrom({ b: 'x', a: 'y' });

    expect(rows.map((row) => row.key)).toEqual(['a', 'b']);
    expect(new Set(rows.map((row) => row.id)).size).toBe(2);
  });
});

describe('flagWireValue', () => {
  it('encodes a fractional value as a float rather than truncating it', () => {
    expect(flagWireValue(entry('temp', 'number', '1.5'))).toBe(1.5);
    expect(flagWireValue(entry('temp', 'number', '-0.25'))).toBe(-0.25);
  });

  it('encodes a whole number as an integer, ignoring surrounding space', () => {
    expect(flagWireValue(entry('effort', 'number', '3'))).toBe(3);
    expect(flagWireValue(entry('effort', 'number', ' 7 '))).toBe(7);
  });

  it('falls back to 0 for a number that cannot be parsed at all', () => {
    expect(flagWireValue(entry('effort', 'number', 'abc'))).toBe(0);
  });

  it('passes a string through verbatim and reads a boolean row strictly', () => {
    expect(flagWireValue(entry('mode', 'string', 'true'))).toBe('true');
    expect(flagWireValue(entry('plan', 'boolean', 'true'))).toBe(true);
    expect(flagWireValue(entry('plan', 'boolean', 'false'))).toBe(false);
  });
});

describe('flagsToWire', () => {
  it('keeps a __proto__ row as an own key instead of touching the prototype', () => {
    const map = flagsToWire([entry('__proto__', 'string', 'x'), entry('model', 'string', 'opus')]);

    expect(Object.hasOwn(map, '__proto__')).toBe(true);
    expect(map['__proto__']).toBe('x');
    expect(Object.getPrototypeOf(map)).toBe(Object.prototype);
    expect(({} as Record<string, unknown>)['x']).toBeUndefined();
  });

  it('builds the map the editor rows describe', () => {
    expect(
      flagsToWire([
        entry('model', 'string', 'opus'),
        entry('plan_mode', 'boolean', 'true'),
        entry('effort', 'number', '3'),
      ]),
    ).toEqual({ model: 'opus', plan_mode: true, effort: 3 });
  });
});

describe('flag signatures', () => {
  it('agrees between the editor rows and the wire map they were seeded from', () => {
    // The pair exists so an untouched editor compares equal to the saved
    // project; a mismatch makes every update resend `default_flags`.
    const flags = { model: 'opus', plan_mode: true, effort: 3, temp: 1.5 };

    expect(flagsSignature(flagEntriesFrom(flags))).toBe(wireFlagsSignature(flags));
  });

  it('changes when a row does', () => {
    const flags = { model: 'opus' };

    expect(flagsSignature([entry('model', 'string', 'sonnet')])).not.toBe(
      wireFlagsSignature(flags),
    );
    expect(flagsSignature([])).toBe(wireFlagsSignature({}));
  });
});

describe('row validation', () => {
  it('flags the second occurrence of a duplicated key, not the first', () => {
    const rows = [entry('dup', 'string', 'a'), entry('dup', 'string', 'b')];

    expect(flagKeyError(rows, 0)).toBeNull();
    expect(flagKeyError(rows, 1)).toBe('Duplicate key');
  });

  it('requires a key', () => {
    expect(flagKeyError([entry('   ', 'string', 'a')], 0)).toBe('Key required');
    expect(flagKeyError([entry('model', 'string', 'a')], 0)).toBeNull();
  });

  it('rejects a boolean row that is not exactly true or false', () => {
    expect(flagValueError(entry('plan', 'boolean', 'yes'))).toBe('Expected true/false');
    expect(flagValueError(entry('plan', 'boolean', 'true'))).toBeNull();
  });

  it('rejects a blank or non-numeric number row', () => {
    expect(flagValueError(entry('effort', 'number', '  '))).toBe('Not a number');
    expect(flagValueError(entry('effort', 'number', 'abc'))).toBe('Not a number');
    expect(flagValueError(entry('effort', 'number', '1.5'))).toBeNull();
  });

  it('accepts any string value, including one that looks like another type', () => {
    expect(flagValueError(entry('mode', 'string', ''))).toBeNull();
    expect(flagValueError(entry('mode', 'string', 'true'))).toBeNull();
  });

  it('reports the whole editor as invalid when any row is', () => {
    expect(flagsValid([entry('model', 'string', 'opus')])).toBe(true);
    expect(flagsValid([entry('', 'string', 'opus')])).toBe(false);
    expect(flagsValid([entry('plan', 'boolean', 'nope')])).toBe(false);
  });
});

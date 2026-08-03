// @vitest-environment node
import { describe, expect, it } from 'vitest';
import {
  parseWireJson,
  stringifyWire,
  toWireSeq,
  WIRE_JSON_SOURCE_ACCESS,
} from './wireJson';

describe('wire JSON', () => {
  it('parses ordinary values exactly as JSON.parse would', () => {
    expect(parseWireJson('{"a":1,"b":[true,null,"x"],"c":1.5}')).toEqual({
      a: 1,
      b: [true, null, 'x'],
      c: 1.5,
    });
  });

  it('throws on malformed input, like JSON.parse', () => {
    expect(() => parseWireJson('{')).toThrow();
  });

  it('leaves a float alone even though it is not a safe integer', () => {
    // `started_at` in the auto-scan snapshot is a float; the promotion guard
    // must key on the literal being integral, not just on isSafeInteger.
    const parsed = parseWireJson('{"started_at":1751800000.5}') as {
      started_at: unknown;
    };
    expect(parsed.started_at).toBe(1751800000.5);
  });

  it('round-trips a sequence number beyond 2^53 without rounding', () => {
    // CI runs Node 22+, which ships the source-access reviver. A runtime
    // without it degrades to a plain double, which is documented and only
    // matters above 2^53 — a magnitude the agent's per-instance counter cannot
    // reach.
    expect(WIRE_JSON_SOURCE_ACCESS).toBe(true);

    const wire = '{"seq":9007199254740993}';
    const parsed = parseWireJson(wire) as { seq: unknown };
    expect(parsed.seq).toBe(9007199254740993n);
    expect(stringifyWire(parsed)).toBe(wire);
  });

  it('emits resume cursors as bare JSON integers, never strings', () => {
    // The server does `int(v)` on resume_from_seq, so quotes or an exponent
    // would be rejected.
    expect(stringifyWire({ resume_from_seq: { 'proj-a': 42n } })).toBe(
      '{"resume_from_seq":{"proj-a":42}}',
    );
  });
});

describe('toWireSeq', () => {
  it('accepts both representations a seq can arrive as', () => {
    expect(toWireSeq(7)).toBe(7n);
    expect(toWireSeq(9007199254740993n)).toBe(9007199254740993n);
  });

  it('treats a missing or unusable seq as zero, like Android and iOS', () => {
    expect(toWireSeq(undefined)).toBe(0n);
    expect(toWireSeq(null)).toBe(0n);
    expect(toWireSeq('12')).toBe(0n);
    expect(toWireSeq(Number.NaN)).toBe(0n);
  });

  it('rejects a fractional seq rather than truncating it', () => {
    // Truncating would advance the durable resume cursor past a sequence
    // number that never arrived, and the real event would then be dropped as a
    // replay and never re-sent.
    expect(toWireSeq(5.9)).toBe(0n);
    expect(toWireSeq(0.5)).toBe(0n);
  });

  it('still accepts a large seq that arrived as a rounded double', () => {
    // The no-source-access fallback: integral but not a *safe* integer.
    expect(toWireSeq(9007199254740994)).toBe(9007199254740994n);
  });
});

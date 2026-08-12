// @vitest-environment node
//
// Node, not jsdom: this reads the shared fixtures off disk with `node:fs`, and
// asserts hex strings rather than typed arrays for the same realm-identity
// reason `terminalCodec.test.ts` documents.
import { describe, expect, it } from 'vitest';
import { bytesToHex, readFixture } from './__fixtures__/load';
import {
  chordDisplay,
  chordToken,
  encodeChord,
  KEY_CHORD_KEYS,
  parseChordToken,
  type KeyChord,
} from './keyChord';

interface EncodeCase {
  name: string;
  chord: KeyChord;
  token: string;
  display: string;
  bytes_hex: string;
  summary: string;
  uses_csi_u: boolean;
  same_as_plain_key: string | null;
}

interface ParseCase {
  name: string;
  token: string;
  expect: KeyChord;
  canonical_token: string;
}

interface KeyChordFixture {
  encode: EncodeCase[];
  encode_rejected: { name: string; chord: KeyChord }[];
  parse_cases: ParseCase[];
  parse_rejected: { name: string; token: string }[];
}

const fixture = readFixture<KeyChordFixture>('keys/key_chords.json');

/**
 * The seven legal (ctrl, alt, shift) combinations — all three together are
 * rejected by contract. Mirrors `legalModifierSubsets` in the Kotlin and Swift
 * arms of these same structural properties.
 */
const LEGAL_MODIFIER_SUBSETS: Pick<KeyChord, 'ctrl' | 'alt' | 'shift'>[] = [
  { ctrl: false, alt: false, shift: false },
  { ctrl: true, alt: false, shift: false },
  { ctrl: false, alt: true, shift: false },
  { ctrl: false, alt: false, shift: true },
  { ctrl: true, alt: true, shift: false },
  { ctrl: true, alt: false, shift: true },
  { ctrl: false, alt: true, shift: true },
];

describe('key chord golden fixtures', () => {
  it('has cases to run', () => {
    expect(fixture.encode.length).toBeGreaterThan(0);
    expect(fixture.encode_rejected.length).toBeGreaterThan(0);
    expect(fixture.parse_cases.length).toBeGreaterThan(0);
    expect(fixture.parse_rejected.length).toBeGreaterThan(0);
  });

  it.each(fixture.encode)('encodes $name to the golden bytes', (encodeCase) => {
    const encoding = encodeChord(encodeCase.chord);
    expect(encoding).not.toBeNull();
    expect(bytesToHex(encoding?.bytes ?? new Uint8Array())).toBe(encodeCase.bytes_hex);
    expect(encoding?.summary).toBe(encodeCase.summary);
    expect(encoding?.usesCsiU).toBe(encodeCase.uses_csi_u);
    expect(encoding?.sameAsPlainKey).toBe(encodeCase.same_as_plain_key);
  });

  it.each(fixture.encode)('renders $name as the golden token and label', (encodeCase) => {
    expect(chordToken(encodeCase.chord)).toBe(encodeCase.token);
    expect(chordDisplay(encodeCase.chord)).toBe(encodeCase.display);
  });

  // The token is what the server stores, so parsing a persisted string and
  // encoding it — which is what the mobile clients' saved-shortcut buttons
  // do — has to reach the same bytes as encoding a composed chord. Both
  // mobile arms assert this.
  it.each(fixture.encode)('encodes the $name token to the same bytes', (encodeCase) => {
    const parsed = parseChordToken(encodeCase.token);
    expect(parsed).not.toBeNull();
    const fromToken = parsed === null ? null : encodeChord(parsed);
    expect(bytesToHex(fromToken?.bytes ?? new Uint8Array())).toBe(encodeCase.bytes_hex);
  });

  it.each(fixture.encode_rejected)('refuses to encode $name', (rejected) => {
    expect(encodeChord(rejected.chord)).toBeNull();
  });

  it.each(fixture.parse_cases)('parses $name', (parseCase) => {
    const chord = parseChordToken(parseCase.token);
    expect(chord).toEqual(parseCase.expect);
    expect(chord === null ? null : chordToken(chord)).toBe(parseCase.canonical_token);
  });

  it.each(fixture.parse_rejected)('refuses to parse $name', (rejected) => {
    expect(parseChordToken(rejected.token)).toBeNull();
  });
});

describe('key chord domain', () => {
  it('covers letters, digits, function keys and the four specials', () => {
    expect(KEY_CHORD_KEYS.letters).toHaveLength(26);
    expect(KEY_CHORD_KEYS.digits).toHaveLength(10);
    expect(KEY_CHORD_KEYS.functionKeys).toEqual([
      'f1',
      'f2',
      'f3',
      'f4',
      'f5',
      'f6',
      'f7',
      'f8',
      'f9',
      'f10',
      'f11',
      'f12',
    ]);
    expect(KEY_CHORD_KEYS.specials).toEqual(['tab', 'enter', 'space', 'backspace']);
    expect(KEY_CHORD_KEYS.all.size).toBe(52);
    expect(KEY_CHORD_KEYS.all.has('esc')).toBe(false);
  });

  // Every chord the picker can compose must encode, or a user could select a
  // combination the Send button then silently drops.
  it('encodes every chord in the domain under every legal modifier subset', () => {
    for (const key of KEY_CHORD_KEYS.all) {
      for (const subset of LEGAL_MODIFIER_SUBSETS) {
        const encoding = encodeChord({ ...subset, key });
        expect(encoding, `${chordToken({ ...subset, key })} must encode`).not.toBeNull();
        expect(encoding?.bytes.length).toBeGreaterThan(0);
      }
    }
  });

  // A CSI-u sequence carries the modifier set explicitly, so two distinct chords
  // can never collapse onto one byte string the way Ctrl+I and Tab do. This has
  // to sweep all seven subsets, not just ctrl × shift: Alt-bearing chords like
  // `alt+shift+tab` and `ctrl+alt+2` reach CSI-u too, and the Kotlin and Swift
  // arms of this same property cover them.
  it('never lets a CSI-u chord collide with another chord', () => {
    const seen = new Map<string, string>();
    for (const key of KEY_CHORD_KEYS.all) {
      for (const subset of LEGAL_MODIFIER_SUBSETS) {
        const chord: KeyChord = { ...subset, key };
        const encoding = encodeChord(chord);
        if (encoding?.usesCsiU !== true) continue;
        const hex = bytesToHex(encoding.bytes);
        expect(seen.get(hex), `${hex} is emitted by more than one chord`).toBeUndefined();
        seen.set(hex, chordToken(chord));
      }
    }
  });
});

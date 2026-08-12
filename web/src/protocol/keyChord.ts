/**
 * Keyboard chords and the legacy-first chord→bytes encoder.
 *
 * Byte-exact port of Android `KeyChordEncoder.kt` / iOS `KeyChordEncoder.swift`.
 * The three implementations are pinned against each other by
 * `tests/protocol_fixtures/keys/key_chords.json` rather than by hand-mirrored
 * assertion tables, so this file must not acquire behaviour the fixture does not
 * describe.
 *
 * It lives under `protocol/` and not `terminal/` for a CI reason as much as an
 * architectural one: `npm run test:protocol` (the arm `protocol-integrity.yml`
 * runs) is scoped to `src/protocol`, and this is a wire contract shared with two
 * other clients.
 */

export interface KeyChord {
  ctrl: boolean;
  alt: boolean;
  shift: boolean;
  key: string;
}

/**
 * The exact wire form of an encoded chord.
 *
 * `summary` renders the bytes for the picker preview ("0x09", "ESC 0x01",
 * "ESC [ 105 ; 6 u"), `usesCsiU` is the single source of truth for the "may not
 * work in every CLI" hint, and `sameAsPlainKey` discloses when a chord collapses
 * onto an unmodified special's byte (Ctrl+I → Tab, Ctrl+M → Enter).
 */
export interface ChordEncoding {
  bytes: Uint8Array;
  summary: string;
  usesCsiU: boolean;
  sameAsPlainKey: string | null;
}

type FunctionKeyForm =
  | { kind: 'ss3'; final: string }
  | { kind: 'tilde'; number: number };

/**
 * The F-key wire forms, and — via its key order — the F-key domain itself. A
 * function key missing from this table would fall through to the legacy path and
 * be encoded as CSI-u, which is a silently wrong sequence rather than a loud
 * failure, so the domain is derived from the table instead of declared beside it.
 *
 * A `Map`, not an object literal: these tables are indexed by a caller-supplied
 * string on the one path in the app that writes bytes to a remote PTY. The
 * whitelist in `encodeChord` already makes a key like `constructor` unreachable,
 * but an object literal would make the guard load-bearing for *safety* rather
 * than merely for correctness, and a `Map` has no prototype chain to inherit.
 */
const FUNCTION_KEY_FORMS = new Map<string, FunctionKeyForm>([
  ['f1', { kind: 'ss3', final: 'P' }],
  ['f2', { kind: 'ss3', final: 'Q' }],
  ['f3', { kind: 'ss3', final: 'R' }],
  ['f4', { kind: 'ss3', final: 'S' }],
  ['f5', { kind: 'tilde', number: 15 }],
  ['f6', { kind: 'tilde', number: 17 }],
  ['f7', { kind: 'tilde', number: 18 }],
  ['f8', { kind: 'tilde', number: 19 }],
  ['f9', { kind: 'tilde', number: 20 }],
  ['f10', { kind: 'tilde', number: 21 }],
  ['f11', { kind: 'tilde', number: 23 }],
  ['f12', { kind: 'tilde', number: 24 }],
]);

const LETTERS: readonly string[] = Array.from({ length: 26 }, (_, i) =>
  String.fromCharCode(0x61 + i),
);
const DIGITS: readonly string[] = Array.from({ length: 10 }, (_, i) => String(i));
const FUNCTION_KEYS: readonly string[] = [...FUNCTION_KEY_FORMS.keys()];

/**
 * Esc is deliberately absent: it is already a control-row key, and Ctrl/Alt+Esc
 * are window-manager chords with no terminal meaning.
 */
const SPECIALS: readonly string[] = ['tab', 'enter', 'space', 'backspace'];

const SPECIAL_SET = new Set(SPECIALS);
const LETTER_SET = new Set(LETTERS);
const DIGIT_SET = new Set(DIGITS);

/** The base-key domain of a saved shortcut chord, as canonical lowercase tokens. */
export const KEY_CHORD_KEYS = {
  letters: LETTERS,
  digits: DIGITS,
  functionKeys: FUNCTION_KEYS,
  specials: SPECIALS,
  all: new Set([...LETTERS, ...DIGITS, ...FUNCTION_KEYS, ...SPECIALS]),
} as const;

export function keyDisplayName(key: string): string {
  if (SPECIAL_SET.has(key)) return key.charAt(0).toUpperCase() + key.slice(1);
  return key.toUpperCase();
}

/** Canonical storage token: modifiers in ctrl, alt, shift order, key last, lowercase. */
export function chordToken(chord: KeyChord): string {
  return (
    (chord.ctrl ? 'ctrl+' : '') +
    (chord.alt ? 'alt+' : '') +
    (chord.shift ? 'shift+' : '') +
    chord.key
  );
}

/** Human label in conventional order, e.g. "Ctrl+Shift+I", "Alt+F4". */
export function chordDisplay(chord: KeyChord): string {
  return (
    (chord.ctrl ? 'Ctrl+' : '') +
    (chord.alt ? 'Alt+' : '') +
    (chord.shift ? 'Shift+' : '') +
    keyDisplayName(chord.key)
  );
}

/**
 * Inverse of {@link chordToken}. Returns null for anything invalid — unknown key
 * or modifier, duplicate modifier, all three modifiers, blank — so callers can
 * silently drop stale or forward-incompatible persisted tokens.
 *
 * Modifiers may arrive in any order; the chord re-emits them canonically.
 */
export function parseChordToken(token: string): KeyChord | null {
  const parts = token.split('+');
  const key = parts.at(-1);
  if (key === undefined || !KEY_CHORD_KEYS.all.has(key)) return null;

  let ctrl = false;
  let alt = false;
  let shift = false;
  for (const modifier of parts.slice(0, -1)) {
    if (modifier === 'ctrl') {
      if (ctrl) return null;
      ctrl = true;
    } else if (modifier === 'alt') {
      if (alt) return null;
      alt = true;
    } else if (modifier === 'shift') {
      if (shift) return null;
      shift = true;
    } else {
      return null;
    }
  }
  if (ctrl && alt && shift) return null;
  return { ctrl, alt, shift, key };
}

interface Raw {
  bytes: Uint8Array;
  summary: string;
  usesCsiU: boolean;
}

function hexByte(byte: number): string {
  return `0x${byte.toString(16).padStart(2, '0').toUpperCase()}`;
}

function singleByte(byte: number): Raw {
  return { bytes: Uint8Array.of(byte), summary: hexByte(byte), usesCsiU: false };
}

/** `afterEsc` is the sequence body following the leading ESC byte. */
function ascii(afterEsc: string, summary: string): Raw {
  const bytes = new Uint8Array(afterEsc.length + 1);
  bytes[0] = 0x1b;
  for (let i = 0; i < afterEsc.length; i += 1) {
    bytes[i + 1] = afterEsc.charCodeAt(i);
  }
  return { bytes, summary, usesCsiU: false };
}

const CSI_U_CODEPOINTS = new Map<string, number>([
  ['tab', 9],
  ['enter', 13],
  ['space', 32],
  ['backspace', 127],
]);

function csiU(key: string, mods: number): Raw {
  // Always the lowercase codepoint — `key` is lowercase in the token domain, so
  // Ctrl+Shift+A reports 97, never 65.
  const codepoint = CSI_U_CODEPOINTS.get(key) ?? key.charCodeAt(0);
  return {
    ...ascii(`[${String(codepoint)};${String(mods)}u`, ''),
    summary: `ESC [ ${String(codepoint)} ; ${String(mods)} u`,
    usesCsiU: true,
  };
}

function functionKeyForm(form: FunctionKeyForm, chord: KeyChord, mods: number): Raw {
  const modified = chord.ctrl || chord.alt || chord.shift;
  if (form.kind === 'ss3') {
    return modified
      ? ascii(`[1;${String(mods)}${form.final}`, `ESC [ 1 ; ${String(mods)} ${form.final}`)
      : ascii(`O${form.final}`, `ESC O ${form.final}`);
  }
  const n = String(form.number);
  return modified
    ? ascii(`[${n};${String(mods)}~`, `ESC [ ${n} ; ${String(mods)} ~`)
    : ascii(`[${n}~`, `ESC [ ${n} ~`);
}

/**
 * The legacy byte for a chord, or null when legacy encoding would silently drop
 * a requested modifier (Ctrl+Shift+letter, any modified digit, Ctrl+Tab,
 * modified Enter, Shift+Space/Backspace).
 */
function legacyCore(chord: KeyChord): Raw | null {
  const { key } = chord;
  if (LETTER_SET.has(key)) {
    const code = key.charCodeAt(0);
    if (chord.ctrl && chord.shift) return null;
    if (chord.ctrl) return singleByte(code & 0x1f);
    if (chord.shift) return singleByte(code - 0x20);
    return singleByte(code);
  }
  if (DIGIT_SET.has(key)) {
    return chord.ctrl || chord.shift ? null : singleByte(key.charCodeAt(0));
  }
  switch (key) {
    case 'tab':
      if (chord.ctrl) return null;
      return chord.shift ? ascii('[Z', 'ESC [ Z') : singleByte(0x09);
    case 'enter':
      return chord.ctrl || chord.shift ? null : singleByte(0x0d);
    case 'space':
      if (chord.shift) return null;
      return singleByte(chord.ctrl ? 0x00 : 0x20);
    case 'backspace':
      if (chord.shift) return null;
      return singleByte(chord.ctrl ? 0x08 : 0x7f);
    default:
      return null;
  }
}

function legacyOrCsiU(chord: KeyChord, mods: number): Raw {
  const core = legacyCore(chord);
  if (core === null) return csiU(chord.key, mods);
  if (!chord.alt) return core;
  // Alt is an ESC prefix and composes only over a single-byte core. Over the
  // multi-byte `CSI Z` core (Alt+Shift+Tab) it cannot, which is the one path
  // where a *successful* legacy core still falls through to CSI-u.
  if (core.bytes.length !== 1) return csiU(chord.key, mods);
  return {
    bytes: Uint8Array.of(0x1b, ...core.bytes),
    summary: `ESC ${core.summary}`,
    usesCsiU: false,
  };
}

const PLAIN_KEY_NAMES: Record<number, string | undefined> = {
  0x09: 'Tab',
  0x0d: 'Enter',
  0x20: 'Space',
  0x7f: 'Backspace',
};

function plainKeyCollision(chord: KeyChord, bytes: Uint8Array): string | null {
  const only = bytes.length === 1 ? bytes[0] : undefined;
  if (only === undefined) return null;
  const name = PLAIN_KEY_NAMES[only];
  if (name === undefined) return null;
  const isThatPlainSpecial =
    !chord.ctrl && !chord.alt && !chord.shift && keyDisplayName(chord.key) === name;
  return isThatPlainSpecial ? null : name;
}

/**
 * Legacy-first chord-to-bytes encoder.
 *
 * Decision procedure, in order:
 *  1. F-keys always use their xterm forms — plain F1–F4 = SS3 `ESC O P..S`,
 *     plain F5–F12 = `CSI 15/17/18/19/20/21/23/24 ~`, and any modifier combo
 *     switches to `CSI 1;m P..S` / `CSI n;m ~`. Never CSI-u, never marked risky.
 *  2. Other keys compute a legacy core from (ctrl, shift, key) — see
 *     {@link legacyCore}.
 *  3. Alt is an ESC prefix, composable only over a single-byte core.
 *  4. Anything rule 2 or 3 rejects falls back to kitty CSI-u
 *     `ESC [ <codepoint> ; <m> u` and is flagged `usesCsiU` — CLIs opt into that
 *     encoding, so it may echo as literal text in legacy-mode terminals.
 */
export function encodeChord(chord: KeyChord): ChordEncoding | null {
  if (!KEY_CHORD_KEYS.all.has(chord.key)) return null;
  if (chord.ctrl && chord.alt && chord.shift) return null;

  const mods =
    1 + (chord.shift ? 1 : 0) + (chord.alt ? 2 : 0) + (chord.ctrl ? 4 : 0);
  const form = FUNCTION_KEY_FORMS.get(chord.key);
  const raw =
    form === undefined ? legacyOrCsiU(chord, mods) : functionKeyForm(form, chord, mods);

  return {
    bytes: raw.bytes,
    summary: raw.summary,
    usesCsiU: raw.usesCsiU,
    sameAsPlainKey: plainKeyCollision(chord, raw.bytes),
  };
}

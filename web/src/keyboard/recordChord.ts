/**
 * Turn a physical key press into a chord the shared grammar can express.
 *
 * The composer records `event.code`, never `event.key`: `code` names the
 * physical key, so a Dvorak or AZERTY user records the key they actually
 * pressed, and Shift does not turn `2` into `@`. `key` would produce a token
 * that means a different key on someone else's layout — and the token syncs to
 * two mobile clients that have no layout at all.
 *
 * Everything the browser cannot express as a contract-valid chord is refused
 * here rather than half-recorded; the picker is the fallback for those.
 */
import { KEY_CHORD_KEYS, type KeyChord } from '../protocol/keyChord';

/** The slice of `KeyboardEvent` the recorder reads. */
export interface RecordableKeyEvent {
  type: string;
  code: string;
  ctrlKey: boolean;
  altKey: boolean;
  shiftKey: boolean;
  metaKey: boolean;
  isComposing?: boolean;
  keyCode?: number;
  getModifierState?: (key: string) => boolean;
}

// Auto-repeat is deliberately absent: a held key re-records the identical chord,
// and React bails out of a state write that changes nothing, so there is nothing
// to suppress.
export type RecordIgnoredReason = 'notKeydown' | 'ime' | 'altGraph' | 'modifierOnly';

export type RecordUnsupportedReason =
  | 'metaKey'
  | 'escape'
  | 'tooManyModifiers'
  | 'unknownKey';

export type RecordOutcome =
  | { status: 'chord'; chord: KeyChord }
  /** Not a press worth reacting to; the recorder stays armed and silent. */
  | { status: 'ignored'; reason: RecordIgnoredReason }
  /** A real press the contract cannot carry; tell the user why. */
  | { status: 'unsupported'; reason: RecordUnsupportedReason; code: string };

const MODIFIER_CODES = new Set([
  'ControlLeft',
  'ControlRight',
  'AltLeft',
  'AltRight',
  'ShiftLeft',
  'ShiftRight',
  'MetaLeft',
  'MetaRight',
  'OSLeft',
  'OSRight',
  'CapsLock',
  'NumLock',
  'ScrollLock',
  'Fn',
  'FnLock',
]);

const NAMED_CODES: Record<string, string | undefined> = {
  Tab: 'tab',
  Enter: 'enter',
  NumpadEnter: 'enter',
  Space: 'space',
  Backspace: 'backspace',
};

/**
 * `KeyA…KeyZ`, `Digit0…Digit9`, `Numpad0…Numpad9` and `F1…F12` map onto the
 * token domain; a numpad digit is the same base key as its top-row twin because
 * the byte the PTY receives is identical.
 */
function baseKeyForCode(code: string): string | null {
  const named = NAMED_CODES[code];
  if (named !== undefined) return named;

  const letter = /^Key([A-Z])$/.exec(code);
  if (letter?.[1] !== undefined) return letter[1].toLowerCase();

  const digit = /^(?:Digit|Numpad)([0-9])$/.exec(code);
  if (digit?.[1] !== undefined) return digit[1];

  const fKey = /^F([1-9]|1[0-2])$/.exec(code);
  if (fKey?.[1] !== undefined) return `f${fKey[1]}`;

  return null;
}

export function recordChordFromEvent(event: RecordableKeyEvent): RecordOutcome {
  if (event.type !== 'keydown') return { status: 'ignored', reason: 'notKeydown' };

  // An IME is mid-composition: `keyCode === 229` is the legacy signal browsers
  // still emit alongside `isComposing`, and reading `code` here would record the
  // physical key behind a candidate selection.
  if (event.isComposing === true || event.keyCode === 229) {
    return { status: 'ignored', reason: 'ime' };
  }

  // Windows AltGr reports Ctrl+Alt, which is indistinguishable from a real
  // Ctrl+Alt chord from the flags alone — so a German user typing `@`
  // (AltGr+Q) would otherwise record `ctrl+alt+q`.
  if (event.getModifierState?.('AltGraph') === true) {
    return { status: 'ignored', reason: 'altGraph' };
  }

  if (MODIFIER_CODES.has(event.code)) {
    return { status: 'ignored', reason: 'modifierOnly' };
  }

  // The chord grammar has no Meta/Cmd modifier, and it cannot grow one without
  // both mobile clients being able to render it.
  if (event.metaKey) {
    return { status: 'unsupported', reason: 'metaKey', code: event.code };
  }

  if (event.code === 'Escape') {
    return { status: 'unsupported', reason: 'escape', code: event.code };
  }

  const modifierCount = [event.ctrlKey, event.altKey, event.shiftKey].filter(Boolean).length;
  if (modifierCount > 2) {
    return { status: 'unsupported', reason: 'tooManyModifiers', code: event.code };
  }

  const baseKey = baseKeyForCode(event.code);
  if (baseKey === null || !KEY_CHORD_KEYS.all.has(baseKey)) {
    return { status: 'unsupported', reason: 'unknownKey', code: event.code };
  }

  return {
    status: 'chord',
    chord: {
      ctrl: event.ctrlKey,
      alt: event.altKey,
      shift: event.shiftKey,
      key: baseKey,
    },
  };
}

/** User-facing explanation for a press the contract cannot carry. */
export function unsupportedMessage(outcome: {
  reason: RecordUnsupportedReason;
  code: string;
}): string {
  switch (outcome.reason) {
    case 'metaKey':
      return 'Shortcuts cannot use Cmd or the Windows key — they have to work on phones too.';
    case 'escape':
      return 'Esc already has its own button, and Ctrl+Esc / Alt+Esc are window-manager chords.';
    case 'tooManyModifiers':
      return 'A shortcut can use at most two of Ctrl, Alt and Shift.';
    case 'unknownKey':
      return `${outcome.code} is not one of A–Z, 0–9, F1–F12, Tab, Enter, Space or Backspace.`;
  }
}

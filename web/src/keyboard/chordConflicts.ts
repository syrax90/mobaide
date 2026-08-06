/**
 * What else already claims a chord the user is about to save.
 *
 * Three classes, all advisory: every one of them still saves, because a
 * contract-valid token has to round-trip to phones that have none of these
 * problems. `severity` says whether typing the chord on *this* machine will
 * reach the PTY; the bar button always will.
 */
import { chordDisplay, encodeChord, type KeyChord } from '../protocol/keyChord';
import type { ControlKey } from '../terminal/controlKeys';
import type { KeyboardEnv } from './browserEnv';
import { lookupReservedKey } from './reservedKeys';

export type ConflictKind =
  | 'duplicate'
  | 'shadowsBuiltIn'
  | 'shadowsApp'
  | 'browserReserved';

export interface ChordConflict {
  kind: ConflictKind;
  /** `warning` = typing it will not reach the terminal here. */
  severity: 'info' | 'warning';
  message: string;
}

export interface ConflictInput {
  chord: KeyChord;
  token: string;
  /** Chord tokens already saved in this session kind's bucket. */
  savedTokens: readonly string[];
  builtInKeys: readonly ControlKey[];
  appTokens: readonly string[];
  env: KeyboardEnv;
}

function sameBytes(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  return a.every((byte, index) => byte === b[index]);
}

export function classifyChordConflicts(input: ConflictInput): ChordConflict[] {
  const conflicts: ChordConflict[] = [];
  const label = chordDisplay(input.chord);

  if (input.savedTokens.includes(input.token)) {
    conflicts.push({
      kind: 'duplicate',
      severity: 'info',
      message: `${label} is already saved for this session type — saving again just renames it.`,
    });
  }

  // Compared on **bytes**, not labels: Ctrl+C and the Ctrl+C button obviously
  // match, but so do Shift+Tab and Mode (both `CSI Z`), and Ctrl+I and Tab.
  const encoding = encodeChord(input.chord);
  if (encoding !== null) {
    const shadowed = input.builtInKeys.find((builtIn) =>
      sameBytes(builtIn.bytes, encoding.bytes),
    );
    if (shadowed !== undefined) {
      conflicts.push({
        kind: 'shadowsBuiltIn',
        severity: 'info',
        message: `${label} sends the same bytes as the built-in ${shadowed.label} button.`,
      });
    }
  }

  if (input.appTokens.includes(input.token)) {
    conflicts.push({
      kind: 'shadowsApp',
      severity: 'warning',
      message: `${label} opens the command palette here, so typing it will not reach the terminal — the bar button still sends it.`,
    });
  }

  const reserved = lookupReservedKey(input.token, input.env);
  if (reserved.entries.length > 0) {
    const actions = [...new Set(reserved.entries.map((entry) => entry.browserAction))].join('; ');
    if (reserved.appSuppressed) {
      // Saying "your browser may take this" here would be exactly backwards:
      // the terminal cancels the browser's default itself while a session is
      // attached, and Ctrl+R is reverse-i-search — a chord people mean to send.
      conflicts.push({
        kind: 'browserReserved',
        severity: 'info',
        message: `${label} normally means "${actions}" in a browser; the terminal suppresses that while a session is attached, so it reaches the program.`,
      });
    } else if (reserved.blocked) {
      conflicts.push({
        kind: 'browserReserved',
        severity: 'warning',
        message: `Your browser or window manager takes ${label} (${actions}) and will not release it — use the bar button instead.`,
      });
    } else {
      conflicts.push({
        kind: 'browserReserved',
        severity: reserved.relaxed ? 'info' : 'warning',
        message: reserved.relaxed
          ? `${label} is a browser shortcut (${actions}); this installed window may still pass it through.`
          : `${label} may be taken by your browser (${actions}) before the terminal sees it.`,
      });
    }
  }

  return conflicts;
}

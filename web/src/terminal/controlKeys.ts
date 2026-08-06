/**
 * The per-session-kind built-in control keys, byte-identical to Android
 * `InputBar.kt` and iOS `InputBar.swift`.
 *
 * On a phone these buttons exist because there is no physical keyboard. On the
 * desktop every one of them is typeable, and the bar survives for the two cases
 * typing does not cover: chords the browser takes first (Ctrl+L is the address
 * bar and Ctrl+O is the file picker on Windows and Linux — see
 * `keyboard/reservedKeys.ts`), and discovering that Mode and Rewind exist at all.
 */

export interface ControlKey {
  /** Unique; also what `orderedControlKeys` filters on. */
  id: string;
  label: string;
  /** Set when `label` is a glyph a screen reader cannot pronounce. */
  ariaLabel?: string;
  bytes: Uint8Array;
  title: string;
}

function key(
  id: string,
  label: string,
  bytes: readonly number[],
  title: string,
  ariaLabel?: string,
): ControlKey {
  return {
    id,
    label,
    ...(ariaLabel === undefined ? {} : { ariaLabel }),
    bytes: Uint8Array.from(bytes),
    title,
  };
}

const ESC = key('esc', 'Esc', [0x1b], 'Escape');
const TAB = key('tab', 'Tab', [0x09], 'Tab');
// DEL (0x7f), not BS (0x08): that is the erase byte readline, Claude Code and
// Codex expect.
const BACKSPACE = key('backspace', '⌫', [0x7f], 'Backspace', 'Backspace');
const MODE = key('mode', 'Mode', [0x1b, 0x5b, 0x5a], 'Shift+Tab — cycle plan / accept-edits / normal');
const REWIND = key('rewind', 'Rewind', [0x1b, 0x1b], 'Esc Esc — edit the previous message');
const ENTER = key('enter', 'Enter', [0x0d], 'Enter');
const CTRL_C = key('ctrlC', 'Ctrl+C', [0x03], 'Interrupt');
// Never mirrored in RTL: a terminal cursor key must point the way it moves the
// cursor, and the bytes are fixed.
const UP = key('up', '↑', [0x1b, 0x5b, 0x41], 'Up', 'Up');
const DOWN = key('down', '↓', [0x1b, 0x5b, 0x42], 'Down', 'Down');
const LEFT = key('left', '←', [0x1b, 0x5b, 0x44], 'Left', 'Left');
const RIGHT = key('right', '→', [0x1b, 0x5b, 0x43], 'Right', 'Right');
const CTRL_O = key('ctrlO', 'Ctrl+O', [0x0f], 'Toggle the transcript');
const CTRL_R = key('ctrlR', 'Ctrl+R', [0x12], 'Search history');
const CTRL_L = key('ctrlL', 'Ctrl+L', [0x0c], 'Clear the screen');
// Bare 'y' / 'n' with no CR — Enter stays a separate key.
const APPROVE = key('approve', 'Approve', [0x79], "Answer 'y'");
const REJECT = key('reject', 'Reject', [0x6e], "Answer 'n'");
const CTRL_K = key('ctrlK', 'Ctrl+K', [0x0b], 'Fast-approve the pending action');
const CTRL_G = key('ctrlG', 'Ctrl+G', [0x07], 'Open the tool-confirmation view');
const CTRL_D = key('ctrlD', 'Ctrl+D', [0x04], 'Exit the CLI');

/**
 * Canonical left-to-right order for every control key. Each kind renders only
 * the subset it supports, filtered from this one list, so a button that appears
 * in two kinds sits in the same place in both — membership is what differs, not
 * position.
 */
const CONTROL_KEY_ORDER: readonly ControlKey[] = [
  ENTER,
  UP,
  DOWN,
  LEFT,
  RIGHT,
  CTRL_C,
  MODE,
  CTRL_L,
  ESC,
  APPROVE,
  REJECT,
  CTRL_K,
  CTRL_G,
  CTRL_D,
  REWIND,
  TAB,
  BACKSPACE,
  CTRL_O,
  CTRL_R,
];

function orderedControlKeys(keys: readonly ControlKey[]): readonly ControlKey[] {
  const present = new Set(keys.map((entry) => entry.id));
  return CONTROL_KEY_ORDER.filter((entry) => present.has(entry.id));
}

/** Nav and control keys every CLI shares. Order here is membership only. */
const SHARED_KEYS: readonly ControlKey[] = [
  ESC,
  CTRL_C,
  ENTER,
  TAB,
  BACKSPACE,
  UP,
  DOWN,
  LEFT,
  RIGHT,
  CTRL_O,
  CTRL_R,
];

const CLAUDE_KEYS = orderedControlKeys([MODE, REWIND, ...SHARED_KEYS, CTRL_L]);
const CODEX_KEYS = orderedControlKeys([APPROVE, REJECT, ...CLAUDE_KEYS]);
const ANTIGRAVITY_KEYS = orderedControlKeys([CTRL_K, CTRL_G, CTRL_D, ...SHARED_KEYS, CTRL_L]);

/**
 * Kind-neutral minimal set. Ctrl+O / Ctrl+R / Ctrl+L are deliberately absent:
 * they carry Claude-TUI meanings an arbitrary CLI binds differently, and the
 * saved-shortcut composer is the escape hatch.
 */
const DEFAULT_KEYS = orderedControlKeys([
  ENTER,
  UP,
  DOWN,
  LEFT,
  RIGHT,
  CTRL_C,
  ESC,
  TAB,
  BACKSPACE,
]);

export const CONTROL_KEY_SETS = {
  claude: CLAUDE_KEYS,
  codex: CODEX_KEYS,
  antigravity: ANTIGRAVITY_KEYS,
  default: DEFAULT_KEYS,
} as const;

/**
 * `kind` is the verbatim wire string, never narrowed to a union — a kind this
 * build does not know about is a *newer server*, not a Claude session.
 *
 * `null` means the session's kind has not resolved yet, and a plain shell gets
 * the same neutral set for the same reason: Mode (`CSI Z`) and Rewind (`ESC ESC`)
 * are Claude-TUI input, and Ctrl+O / Ctrl+R / Ctrl+L are readline bindings a user
 * may have remapped (CLA-158, fixed on all three clients together).
 */
export function controlKeysFor(kind: string | null): readonly ControlKey[] {
  switch (kind) {
    case 'codex':
      return CODEX_KEYS;
    case 'antigravity':
      return ANTIGRAVITY_KEYS;
    case 'claude':
    case 'auto_scan':
      return CLAUDE_KEYS;
    default:
      return DEFAULT_KEYS;
  }
}

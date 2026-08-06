/**
 * Chords the app keeps for itself instead of forwarding to the PTY — VS Code's
 * `terminal.integrated.commandsToSkipShell`, one entry long.
 *
 * Stealing any chord from a terminal costs the user that chord, so the choice is
 * deliberate. **Ctrl+Shift+P**, VS Code's own palette chord: it is not in any
 * built-in control-key set, and for the terminal it is only a CSI-u sequence
 * almost nothing binds. The issue text proposed Ctrl+K with an F1 alias; both
 * were rejected, because `ctrl+k` encodes to `0x0B` — Antigravity's built-in
 * fast-approve key and readline's kill-line — and plain `f1` is a TUI help key
 * that users can save as a shortcut in its own right.
 *
 * macOS gets Cmd+Shift+P instead, which is what VS Code does there and what
 * keeps Ctrl+Shift+P available to the terminal on that platform — hence
 * {@link appShortcutTokens} returning nothing on a Mac.
 */
import { chordDisplay, chordToken, type KeyChord } from '../protocol/keyChord';
import type { KeyboardEnv } from './browserEnv';

export type AppCommand = 'commandPalette';

/**
 * The single definition of the palette chord. The token the conflict checker
 * compares against, the label the button shows and the physical key the matcher
 * tests are all derived from this, so they cannot drift apart.
 */
const PALETTE_CHORD: KeyChord = { ctrl: true, alt: false, shift: true, key: 'p' };

/** `KeyboardEvent.code` for {@link PALETTE_CHORD}'s base key, pinned by its test. */
export const PALETTE_CHORD_CODE = 'KeyP';

/** The chord itself, exported so tests can tie the code above back to the key. */
export const PALETTE_CHORD_KEY = PALETTE_CHORD.key;

/** The slice of `KeyboardEvent` the matcher reads. */
export interface AppShortcutEvent {
  type: string;
  code: string;
  ctrlKey: boolean;
  altKey: boolean;
  shiftKey: boolean;
  metaKey: boolean;
}

export interface AppShortcutDescription {
  command: AppCommand;
  label: string;
  /** What to show the user, per platform. */
  hint: (os: KeyboardEnv['os']) => string;
}

export const APP_SHORTCUTS: readonly AppShortcutDescription[] = [
  {
    command: 'commandPalette',
    label: 'Command palette',
    hint: (os) => (os === 'mac' ? '⌘⇧P' : chordDisplay(PALETTE_CHORD)),
  },
];

/** The description for a command, for rendering its hint next to a button. */
export function appShortcutFor(command: AppCommand): AppShortcutDescription | undefined {
  return APP_SHORTCUTS.find((shortcut) => shortcut.command === command);
}

/**
 * Which chord tokens the app takes in this environment, for the composer's
 * "shadows an app shortcut" check. Empty on macOS, where the palette is on Cmd
 * and Cmd chords are outside the shared token grammar entirely.
 */
export function appShortcutTokens(env: KeyboardEnv): readonly string[] {
  return env.os === 'mac' ? [] : [chordToken(PALETTE_CHORD)];
}

/**
 * Matched on `event.code`, not `event.key`, so a Dvorak or AZERTY layout finds
 * the palette in the same physical place — the same rule the chord recorder
 * follows.
 */
export function matchAppShortcut(
  event: AppShortcutEvent,
  env: KeyboardEnv,
): AppCommand | null {
  if (event.type !== 'keydown') return null;
  if (event.code !== PALETTE_CHORD_CODE || !event.shiftKey || event.altKey) return null;
  const primary =
    env.os === 'mac' ? event.metaKey && !event.ctrlKey : event.ctrlKey && !event.metaKey;
  return primary ? 'commandPalette' : null;
}

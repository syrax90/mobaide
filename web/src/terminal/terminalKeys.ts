/**
 * Desktop copy/paste key policy.
 *
 * **This has no mobile counterpart to port.** On Android and iOS `Ctrl+C` is a
 * button on the control row and is unconditionally `0x03`; copy and paste are
 * reachable only from the long-press selection toolbar, and neither client has
 * a "pass Ctrl+V to the terminal" preference. A physical keyboard is a browser /
 * desktop platform convention, which is the case the cross-platform parity rule
 * in `CLAUDE.md` exempts, so this ships web-only with no mirror issue.
 *
 * Three outcomes, matching what `attachCustomKeyEventHandler` can express:
 *
 * - `copy` — we write the selection to the clipboard and swallow the event.
 * - `browserPaste` — hands off entirely: xterm does not process it and we do
 *   **not** `preventDefault`, so the browser performs its native paste and the
 *   resulting `paste` event carries the clipboard data. That is deliberately
 *   more reliable than `navigator.clipboard.readText()`, which Firefox and
 *   Safari gate behind a per-read user prompt.
 * - `terminal` — ordinary xterm processing.
 */

export type TerminalKeyAction = 'copy' | 'browserPaste' | 'terminal';

/**
 * The slice of `KeyboardEvent` the policy reads. Unrelated to the `KeyChord` in
 * `protocol/keyChord.ts`, which is a saveable Ctrl/Alt/Shift + base-key chord;
 * this one is just the modifier flags of a browser event.
 */
export interface KeyEventChord {
  type: string;
  key: string;
  ctrlKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
  metaKey: boolean;
}

export interface KeyPolicyContext {
  hasSelection: boolean;
  /**
   * When true, `Ctrl+V` reaches the program as `0x16` instead of pasting.
   * Claude Code binds it for image paste, so the toggle has to exist; it is off
   * by default because pasting is what the chord means everywhere else.
   */
  passCtrlVToTerminal: boolean;
  isMac: boolean;
}

export function resolveKeyAction(
  chord: KeyEventChord,
  context: KeyPolicyContext,
): TerminalKeyAction {
  if (chord.type !== 'keydown') return 'terminal';
  const key = chord.key.toLowerCase();

  if (context.isMac && chord.metaKey && !chord.ctrlKey && !chord.altKey) {
    if (key === 'c') return context.hasSelection ? 'copy' : 'terminal';
    if (key === 'v') return 'browserPaste';
    return 'terminal';
  }

  if (chord.metaKey || chord.altKey) return 'terminal';

  if (chord.ctrlKey && chord.shiftKey) {
    // Unconditional, unlike bare Ctrl+C: `Ctrl+Shift+C` on an empty selection
    // must be a no-op, never a SIGINT. xterm ignores Shift when encoding a
    // control letter, so falling through would send `0x03` and kill the job.
    if (key === 'c') return 'copy';
    if (key === 'v') return 'browserPaste';
    return 'terminal';
  }

  if (chord.ctrlKey && !chord.shiftKey) {
    if (key === 'c') return context.hasSelection ? 'copy' : 'terminal';
    if (key === 'v') {
      return context.passCtrlVToTerminal ? 'terminal' : 'browserPaste';
    }
  }

  return 'terminal';
}

/** Whether the platform uses Cmd rather than Ctrl for clipboard chords. */
export function detectIsMac(platform: string): boolean {
  return /mac|iphone|ipad|ipod/i.test(platform);
}

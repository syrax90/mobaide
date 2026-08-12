/**
 * Clipboard-to-PTY encoder — a byte-exact mirror of Android
 * `TerminalEmulator.paste()` and iOS `TerminalPaste`.
 *
 * The order is load-bearing and is the escape-injection defense: stripping ESC
 * and C1 *before* wrapping means clipboard text carrying a live `ESC[201~`
 * degrades to the literal `[201~` and cannot close the bracket early, leaving
 * its tail to execute as direct input (the MinTTY/Xshell CVE-2021-31701 class).
 *
 * xterm.js's own paste path (`prepareTextForTerminal` + `bracketTextForPaste`)
 * does the newline pass and the wrap but **no strip**, which is why the terminal
 * view intercepts the `paste` event in the capture phase and routes it here
 * instead of letting `Terminal.paste()` run.
 */

/** ESC (0x1b) and the C1 range [0x80…0x9f]; everything else survives. */
// The control characters are the whole point of this class, so the rule that
// exists to catch accidental ones has nothing to say here.
// eslint-disable-next-line no-control-regex
const ESCAPE_AND_C1 = /[\u001b\u0080-\u009f]/g;

/** DECSET 2004 markers. */
export const BRACKETED_PASTE_START = '\u001b[200~';
export const BRACKETED_PASTE_END = '\u001b[201~';

/**
 * Largest sanitized body that may go on the wire, markers excluded.
 *
 * Inherited from iOS, which added it as a deliberate divergence from Android
 * (which has no cap): the agent opens its relay tunnel with `max_size=4 MB`
 * (`relay/client.py`), and one oversized frame trips `PayloadTooBig` and closes
 * that tunnel with 1009 — taking down **every** session on the agent, not just
 * this one. A megabyte is far past any real paste into a prompt.
 */
export const MAX_PASTE_BYTES = 1 << 20;

const utf8 = new TextEncoder();

/**
 * Drop ESC and C1 without touching anything else.
 *
 * Split out from {@link sanitizePaste} because the execute builder needs the
 * escape-injection strip without the newline rewrite.
 */
export function stripControls(text: string): string {
  return text.replace(ESCAPE_AND_C1, '');
}

/**
 * Step 1 — {@link stripControls}. Step 2 — collapse `\r\n` and `\n` to a single
 * `\r`; a lone `\r` is left alone.
 *
 * Two ordered passes, exactly as Android runs them. A single fused walk is
 * tempting but *not* equivalent: for `"\r" ESC "\n"` Android strips first and
 * sees `\r\n`, one line break, while a fused walk emits the `\r`, drops the ESC,
 * then meets a bare `\n` and emits a second `\r`.
 */
export function sanitizePaste(text: string): string {
  return stripControls(text).replace(/\r?\n/g, '\r');
}

/** Measure a payload the way the wire will: UTF-8 bytes, not UTF-16 units. */
export function measureUtf8Bytes(text: string): number {
  return utf8.encode(text).length;
}

/**
 * What to do with an encoded payload. Shared by the paste encoder and the
 * execute builder, which are bound by the same relay frame ceiling.
 */
export type PayloadOutcome =
  | { kind: 'send'; text: string }
  /** Nothing survived the sanitize — no frame, no message. */
  | { kind: 'empty' }
  /** Over {@link MAX_PASTE_BYTES}; the caller tells the user why. */
  | { kind: 'tooLarge'; bytes: number };

/**
 * Encode a clipboard payload for the wire.
 *
 * Emits **one** string, not Android's three separate writes: these cross a
 * WebSocket and a relay rather than a local PTY fd, and losing only the closing
 * `ESC[201~` would strand the program's line editor in paste-collect mode and
 * read to the user as a dead keyboard. iOS made the same call.
 */
export function encodePaste(text: string, bracketed: boolean): PayloadOutcome {
  const body = sanitizePaste(text);
  if (body.length === 0) return { kind: 'empty' };
  const bytes = measureUtf8Bytes(body);
  if (bytes > MAX_PASTE_BYTES) return { kind: 'tooLarge', bytes };
  return {
    kind: 'send',
    text: bracketed ? BRACKETED_PASTE_START + body + BRACKETED_PASTE_END : body,
  };
}

/**
 * Composer and selection payload builders for the `m` (submit) and `x`
 * (execute) frames.
 *
 * Mirrors Android `TerminalViewModel.sendInput` / `sendTappedRow` and iOS
 * `TerminalSessionModel.sendInput` / the row-tap path. Neither frame carries a
 * trailing CR: since proto-v4 the agent owns that timing and writes the CR as a
 * separate PTY write, so an Ink TUI parses it as a discrete Enter rather than as
 * pasted content.
 *
 * Both builders share the paste path's 1 MiB ceiling. That cap is an
 * availability control, not a nicety: the relay forwards a client frame to the
 * agent's tunnel unchunked and unchecked, and the agent opens that tunnel with
 * `max_size=4 MB`, so one oversized frame raises `PayloadTooBig` and closes it
 * with 1009 — dropping **every** session on that agent. Neither path is exotic:
 * "Send to input" copies an arbitrarily large selection into the composer, and
 * "Run" reads a selection spanning up to 10,000 scrollback rows.
 */
import {
  BRACKETED_PASTE_END,
  BRACKETED_PASTE_START,
  MAX_PASTE_BYTES,
  measureUtf8Bytes,
  stripControls,
  type PayloadOutcome,
} from './terminalPaste';

/**
 * `Ctrl+A Ctrl+K` — readline "start of line" then "kill to end of line".
 *
 * Prefixed to every submit so a send never concatenates onto stale prompt text.
 * Deliberately not `Ctrl+C`: these clear the line without raising SIGINT, so a
 * running task is not interrupted by someone sending a queued message.
 */
export const CLEAR_INPUT_LINE = '\u0001\u000b';

function withinCap(text: string): PayloadOutcome {
  const bytes = measureUtf8Bytes(text);
  return bytes > MAX_PASTE_BYTES ? { kind: 'tooLarge', bytes } : { kind: 'send', text };
}

/**
 * Body of an `m` submit frame.
 *
 * Multi-line bodies are bracket-wrapped; single-line ones stay raw, because a
 * shell that never enabled DECSET 2004 would echo a literal `[200~`. The ESC/C1
 * strip is the same escape-injection defense the paste encoder applies — without
 * it a template or a pasted composer body could close the bracket early.
 */
export function submitPayload(text: string): PayloadOutcome {
  const body = stripControls(text);
  if (body.trim().length === 0) return { kind: 'empty' };
  const wrapped = body.includes('\n')
    ? BRACKETED_PASTE_START + body + BRACKETED_PASTE_END
    : body;
  return withinCap(CLEAR_INPUT_LINE + wrapped);
}

/**
 * Body of an `x` execute frame — the agent sends Ctrl+C, pauses, writes this,
 * pauses, then writes the CR.
 *
 * Collapsed to a single line: execute replays one command, and a multi-line
 * selection would otherwise run every line of it.
 */
export function executePayload(text: string): PayloadOutcome {
  const body = stripControls(text).replace(/\r?\n/g, ' ').trim();
  return body.length === 0 ? { kind: 'empty' } : withinCap(body);
}

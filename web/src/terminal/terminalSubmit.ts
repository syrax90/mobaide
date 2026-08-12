/**
 * Selection payload builder for the `x` (execute) frame.
 *
 * Mirrors Android `TerminalViewModel.sendTappedRow` and iOS's row-tap path.
 * The frame carries no trailing CR: since proto-v4 the agent owns that timing
 * and writes the CR as a separate PTY write, so an Ink TUI parses it as a
 * discrete Enter rather than as pasted content.
 *
 * Shares the paste path's 1 MiB ceiling. That cap is an availability control,
 * not a nicety: the relay forwards a client frame to the agent's tunnel
 * unchunked and unchecked, and the agent opens that tunnel with `max_size=4 MB`,
 * so one oversized frame raises `PayloadTooBig` and closes it with 1009 —
 * dropping **every** session on that agent. The path is not exotic: "Run" reads
 * a selection spanning up to 10,000 scrollback rows.
 */
import {
  MAX_PASTE_BYTES,
  measureUtf8Bytes,
  stripControls,
  type PayloadOutcome,
} from './terminalPaste';

function withinCap(text: string): PayloadOutcome {
  const bytes = measureUtf8Bytes(text);
  return bytes > MAX_PASTE_BYTES ? { kind: 'tooLarge', bytes } : { kind: 'send', text };
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

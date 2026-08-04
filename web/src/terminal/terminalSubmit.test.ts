import { describe, expect, it } from 'vitest';
import {
  BRACKETED_PASTE_END,
  BRACKETED_PASTE_START,
  MAX_PASTE_BYTES,
  type PayloadOutcome,
} from './terminalPaste';
import { CLEAR_INPUT_LINE, executePayload, submitPayload } from './terminalSubmit';

const ESC = '\u001b';

const codePoints = (text: string): number[] =>
  [...text].map((character) => character.codePointAt(0) ?? -1);

const requireSend = (outcome: PayloadOutcome): string => {
  if (outcome.kind !== 'send') throw new Error(`expected a "send" outcome, got "${outcome.kind}"`);
  return outcome.text;
};

describe('CLEAR_INPUT_LINE', () => {
  it('is Ctrl+A then Ctrl+K, never Ctrl+C', () => {
    // Both clear the line, but `0x03` would raise SIGINT and kill a running
    // task just because someone sent a queued message.
    expect(codePoints(CLEAR_INPUT_LINE)).toEqual([0x01, 0x0b]);
  });
});

describe('submitPayload', () => {
  it('prefixes the line clear and leaves a single-line body unwrapped', () => {
    // A shell that never enabled DECSET 2004 would echo a literal `[200~`.
    expect(submitPayload('list the open PRs')).toEqual({
      kind: 'send',
      text: `${CLEAR_INPUT_LINE}list the open PRs`,
    });
  });

  it('wraps a multi-line body in the bracketed paste markers', () => {
    expect(submitPayload('first\nsecond')).toEqual({
      kind: 'send',
      text: `${CLEAR_INPUT_LINE}${BRACKETED_PASTE_START}first\nsecond${BRACKETED_PASTE_END}`,
    });
  });

  it('strips escapes before wrapping so a body cannot close the bracket early', () => {
    expect(submitPayload(`a\n${ESC}[201~rm -rf /`)).toEqual({
      kind: 'send',
      text: `${CLEAR_INPUT_LINE}${BRACKETED_PASTE_START}a\n[201~rm -rf /${BRACKETED_PASTE_END}`,
    });
  });

  it('never appends a carriage return', () => {
    // Since proto-v4 the agent writes the CR as a separate PTY write, so an
    // Ink TUI reads it as a discrete Enter rather than as pasted content.
    expect(requireSend(submitPayload('echo hi')).endsWith('\r')).toBe(false);
    expect(requireSend(submitPayload('a\nb')).endsWith('\r')).toBe(false);
  });

  it('reports an empty outcome for a body with nothing in it', () => {
    expect(submitPayload('')).toEqual({ kind: 'empty' });
    expect(submitPayload('   \t ')).toEqual({ kind: 'empty' });
    expect(submitPayload('\n\n')).toEqual({ kind: 'empty' });
    expect(submitPayload(ESC)).toEqual({ kind: 'empty' });
  });

  it('measures the cap over the encoded frame, line clear included', () => {
    // "Send to input" drops an arbitrarily large selection into the composer,
    // so the composer is as exposed to the relay's frame ceiling as paste is:
    // one oversized frame trips the agent tunnel's 4 MB `max_size` and drops
    // every session on that agent. What goes on the wire is the prefix plus the
    // body, so that is what has to be under the cap.
    const atCap = 'a'.repeat(MAX_PASTE_BYTES - CLEAR_INPUT_LINE.length);

    expect(submitPayload(atCap).kind).toBe('send');
    expect(submitPayload(`${atCap}a`)).toEqual({
      kind: 'tooLarge',
      bytes: MAX_PASTE_BYTES + 1,
    });
  });
});

describe('executePayload', () => {
  it('collapses the body to one trimmed line', () => {
    // Execute replays one command; a multi-line selection would otherwise run
    // every line of it.
    expect(executePayload(' echo hi\r\nls \n')).toEqual({ kind: 'send', text: 'echo hi ls' });
  });

  it('never wraps or terminates the command', () => {
    const payload = executePayload('a\nb');

    expect(requireSend(payload)).toBe('a b');
    expect(requireSend(payload).includes(BRACKETED_PASTE_START)).toBe(false);
    expect(requireSend(payload).endsWith('\r')).toBe(false);
  });

  it('strips escapes from the command', () => {
    expect(executePayload(`git ${ESC}[201~status`)).toEqual({
      kind: 'send',
      text: 'git [201~status',
    });
  });

  it('reports an empty outcome when nothing is left to run', () => {
    expect(executePayload('')).toEqual({ kind: 'empty' });
    expect(executePayload('  \n ')).toEqual({ kind: 'empty' });
    expect(executePayload(ESC)).toEqual({ kind: 'empty' });
  });

  it('refuses a selection over the cap', () => {
    // "Run" reads a selection spanning up to 10,000 scrollback rows, so this is
    // reachable without an attacker.
    expect(executePayload('a'.repeat(MAX_PASTE_BYTES)).kind).toBe('send');
    expect(executePayload('a'.repeat(MAX_PASTE_BYTES + 1))).toEqual({
      kind: 'tooLarge',
      bytes: MAX_PASTE_BYTES + 1,
    });
  });
});

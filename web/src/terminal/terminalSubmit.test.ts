import { describe, expect, it } from 'vitest';
import {
  BRACKETED_PASTE_START,
  MAX_PASTE_BYTES,
  type PayloadOutcome,
} from './terminalPaste';
import { executePayload } from './terminalSubmit';

const ESC = '\u001b';

const requireSend = (outcome: PayloadOutcome): string => {
  if (outcome.kind !== 'send') throw new Error(`expected a "send" outcome, got "${outcome.kind}"`);
  return outcome.text;
};

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

import { describe, expect, it } from 'vitest';
import {
  BRACKETED_PASTE_END,
  BRACKETED_PASTE_START,
  encodePaste,
  MAX_PASTE_BYTES,
  sanitizePaste,
  stripControls,
} from './terminalPaste';

const ESC = '\u001b';

describe('stripControls', () => {
  it('drops ESC and the C1 range and nothing else', () => {
    expect(stripControls(`a${ESC}b\u0080c\u009fd`)).toBe('abcd');
    // Tabs, both line-break bytes, DEL and non-ASCII text all survive: the
    // strip exists for escape injection, not for tidying the payload.
    expect(stripControls('a\tb\r\nc\u007fd é 日本')).toBe('a\tb\r\nc\u007fd é 日本');
  });
});

describe('sanitizePaste', () => {
  it('runs the strip before the newline pass', () => {
    // The reason the two passes cannot be fused: stripping first leaves `\r\n`,
    // one line break. A fused walk emits the `\r`, drops the ESC, then meets a
    // bare `\n` and emits a second one.
    expect(sanitizePaste(`a\r${ESC}\nb`)).toBe('a\rb');
  });

  it('collapses both line-break forms to a single CR and leaves a lone CR alone', () => {
    expect(sanitizePaste('a\r\nb')).toBe('a\rb');
    expect(sanitizePaste('a\nb')).toBe('a\rb');
    expect(sanitizePaste('a\rb')).toBe('a\rb');
    expect(sanitizePaste('a\n\nb')).toBe('a\r\rb');
  });
});

describe('encodePaste', () => {
  it('wraps the body in the DECSET 2004 markers only when the mode is on', () => {
    expect(encodePaste('hello', true)).toEqual({
      kind: 'send',
      text: `${BRACKETED_PASTE_START}hello${BRACKETED_PASTE_END}`,
    });
    expect(encodePaste('hello', false)).toEqual({ kind: 'send', text: 'hello' });
  });

  it('degrades an embedded end marker so it cannot close the bracket early', () => {
    // The CVE-2021-31701 class: with the strip after the wrap, the tail of this
    // clipboard payload would leave paste mode and execute as direct input.
    const outcome = encodePaste(`safe${ESC}[201~rm -rf /`, true);

    expect(outcome).toEqual({
      kind: 'send',
      text: `${BRACKETED_PASTE_START}safe[201~rm -rf /${BRACKETED_PASTE_END}`,
    });
  });

  it('reports an empty outcome when nothing survives the sanitize', () => {
    expect(encodePaste('', true)).toEqual({ kind: 'empty' });
    expect(encodePaste(`${ESC}${ESC}\u0090`, true)).toEqual({ kind: 'empty' });
  });

  it('sends a body of exactly the cap and refuses one byte more', () => {
    expect(encodePaste('a'.repeat(MAX_PASTE_BYTES), false).kind).toBe('send');
    expect(encodePaste('a'.repeat(MAX_PASTE_BYTES + 1), false)).toEqual({
      kind: 'tooLarge',
      bytes: MAX_PASTE_BYTES + 1,
    });
  });

  it('measures the cap in UTF-8 bytes, not in characters', () => {
    // The limit exists because one oversized frame trips the agent tunnel's
    // 4 MB `max_size` and drops every session on that agent, and the tunnel
    // counts bytes. Half the cap in two-byte characters is already over it.
    const twoByteChars = 'é'.repeat(MAX_PASTE_BYTES / 2 + 1);

    expect(twoByteChars.length).toBeLessThan(MAX_PASTE_BYTES);
    expect(encodePaste(twoByteChars, false)).toEqual({
      kind: 'tooLarge',
      bytes: MAX_PASTE_BYTES + 2,
    });
  });
});

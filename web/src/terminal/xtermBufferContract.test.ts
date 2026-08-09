// @vitest-environment node
//
// The tripwire for an xterm.js version bump.
//
// `terminalFollow` is derived from three `BufferService.scroll` behaviours at
// tag 6.0.0 and reads nothing but the two public buffer indices. If a later
// xterm changes any of them the unread tally goes wrong *silently* — no type
// error, no crash, just a badge that stops counting — so the behaviours are
// pinned against a real emulator rather than restated in a comment.
//
// Node, not jsdom: `@xterm/headless` is the Node build of the same core.
import { Terminal } from '@xterm/headless';
import { afterEach, describe, expect, it } from 'vitest';
import { accrueScrollbackRows, isPinned, type ScrollSample } from './terminalFollow';
import { CLEAR_BEFORE_REPLAY, CLEAR_SCROLLBACK } from './terminalReplay';

const live: Terminal[] = [];

afterEach(() => {
  for (const terminal of live.splice(0)) terminal.dispose();
});

/** `allowProposedApi` is mandatory here — `terminal.buffer` throws without it. */
function headless(rows: number, cols: number, scrollback: number): Terminal {
  const terminal = new Terminal({ allowProposedApi: true, rows, cols, scrollback });
  live.push(terminal);
  return terminal;
}

/** `write` is asynchronous; every assertion waits for the parser to drain. */
function write(terminal: Terminal, data: string | Uint8Array): Promise<void> {
  return new Promise((resolve) => {
    terminal.write(data, resolve);
  });
}

function sample(terminal: Terminal): ScrollSample {
  return {
    baseY: terminal.buffer.active.baseY,
    viewportY: terminal.buffer.active.viewportY,
  };
}

async function writeLines(terminal: Terminal, count: number): Promise<void> {
  for (let index = 0; index < count; index += 1) {
    await write(terminal, `line${String(index)}\r\n`);
  }
}

describe('xterm buffer indices that the unread tally reads', () => {
  it('grows baseY and holds viewportY while the scrollback ring has room', async () => {
    const terminal = headless(5, 20, 10);
    await writeLines(terminal, 6);
    terminal.scrollLines(-3);

    const before = sample(terminal);
    expect(before).toEqual({ baseY: 2, viewportY: 0 });
    expect(isPinned(before)).toBe(false);

    await writeLines(terminal, 2);
    const after = sample(terminal);

    expect(after).toEqual({ baseY: 4, viewportY: 0 });
    expect(accrueScrollbackRows(before, after)).toBe(2);
  });

  it('freezes baseY and decrements viewportY once the ring is full', async () => {
    // The case that makes a naive baseY-delta counter stop counting: 5 rows
    // plus 10 of scrollback saturates at baseY 10 and never moves again.
    const terminal = headless(5, 20, 10);
    await writeLines(terminal, 40);
    terminal.scrollLines(-5);

    const before = sample(terminal);
    expect(before).toEqual({ baseY: 10, viewportY: 5 });

    await writeLines(terminal, 3);
    const after = sample(terminal);

    expect(after).toEqual({ baseY: 10, viewportY: 2 });
    expect(after.baseY - before.baseY).toBe(0);
    expect(accrueScrollbackRows(before, after)).toBe(3);
  });

  it('moves neither index for a DECSTBM region scroll', async () => {
    // Rows scrolled inside a margin region are shifted in place and never
    // reach the scrollback, so they are not unread. iOS agrees; Android counts
    // them because its Termux fork really does append them.
    const terminal = headless(8, 20, 10);
    await writeLines(terminal, 8);
    terminal.scrollLines(-2);
    await write(terminal, '\u001b[2;5r');
    await write(terminal, '\u001b[5;1H');

    const before = sample(terminal);
    await write(terminal, '\n\n\n');
    const after = sample(terminal);

    expect(after).toEqual(before);
    expect(accrueScrollbackRows(before, after)).toBe(0);
  });

  it('resets both indices on the replay wipe', async () => {
    // Why the model re-baselines on `replayStart` instead of sampling: the
    // wipe drops the scrollback, which a raw accrue reads as 12 unread rows.
    const terminal = headless(5, 20, 100);
    await writeLines(terminal, 20);
    terminal.scrollLines(-4);

    const before = sample(terminal);
    expect(before).toEqual({ baseY: 16, viewportY: 12 });

    await write(terminal, CLEAR_BEFORE_REPLAY);
    const after = sample(terminal);

    expect(after).toEqual({ baseY: 0, viewportY: 0 });
    expect(accrueScrollbackRows(before, after)).toBe(12);
  });

  it('trims only the scrollback on a bare ESC[3J, keeping the viewport painted', async () => {
    // The Ctrl+L local wipe relies on ED 3 leaving the active screen intact:
    // only the scrollback ring is dropped, with both indices shifted down by
    // the trimmed amount. The raw accrue reads that shift as unread rows,
    // which is why `sendControlKey` re-baselines before the write.
    const terminal = headless(5, 20, 100);
    await writeLines(terminal, 20);
    terminal.scrollLines(-4);

    const before = sample(terminal);
    expect(before).toEqual({ baseY: 16, viewportY: 12 });

    await write(terminal, CLEAR_SCROLLBACK);
    const after = sample(terminal);

    expect(after).toEqual({ baseY: 0, viewportY: 0 });
    // The active rows survive — unlike the replay wipe, nothing blanks them.
    expect(terminal.buffer.active.getLine(0)?.translateToString(true)).toBe('line16');
    expect(terminal.buffer.active.getLine(3)?.translateToString(true)).toBe('line19');
    expect(accrueScrollbackRows(before, after)).toBe(12);
  });
});

describe('xterm modes the terminal screen gates on', () => {
  it('reports synchronized output mode through terminal.modes', async () => {
    const terminal = headless(5, 20, 10);
    expect(terminal.modes.synchronizedOutputMode).toBe(false);

    await write(terminal, '\u001b[?2026h');
    expect(terminal.modes.synchronizedOutputMode).toBe(true);

    await write(terminal, '\u001b[?2026l');
    expect(terminal.modes.synchronizedOutputMode).toBe(false);
  });

  it('reports bracketed paste mode through terminal.modes', async () => {
    const terminal = headless(5, 20, 10);
    expect(terminal.modes.bracketedPasteMode).toBe(false);

    await write(terminal, '\u001b[?2004h');
    expect(terminal.modes.bracketedPasteMode).toBe(true);
  });
});

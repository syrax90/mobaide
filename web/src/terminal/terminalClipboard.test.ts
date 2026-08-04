import type { Terminal } from '@xterm/xterm';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  isOsc52Read,
  refuseOsc52Reads,
  TerminalClipboardProvider,
  writeClipboardText,
} from './terminalClipboard';

const writeText = vi.fn<(text: string) => Promise<void>>();

beforeEach(() => {
  writeText.mockReset();
  writeText.mockResolvedValue(undefined);
  // jsdom ships no `navigator.clipboard`; install one for the duration.
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText },
  });
});

afterEach(() => {
  Reflect.deleteProperty(navigator, 'clipboard');
});

describe('writeClipboardText', () => {
  it('reports a completed write', async () => {
    await expect(writeClipboardText('hello')).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith('hello');
  });

  it('swallows the refusal Safari and Firefox give a gesture-less write', async () => {
    // An unhandled rejection here would surface inside xterm's parser promise
    // chain, which is where OSC 52 writes are dispatched from.
    writeText.mockRejectedValue(new Error('NotAllowedError'));

    await expect(writeClipboardText('hello')).resolves.toBe(false);
  });
});

describe('TerminalClipboardProvider', () => {
  it('reports an empty clipboard rather than the real one', () => {
    // A program that can read the clipboard can exfiltrate whatever the user
    // copied last, which on a shared machine is routinely a password.
    expect(new TerminalClipboardProvider().readText()).toBe('');
    expect(writeText).not.toHaveBeenCalled();
  });

  it('writes only to the system selection', () => {
    const provider = new TerminalClipboardProvider();

    provider.writeText('c', 'copied');
    expect(writeText).toHaveBeenCalledWith('copied');

    // A browser has no analogue of X11's primary selection, so a program
    // targeting it must not have its bytes land in the system clipboard.
    writeText.mockClear();
    provider.writeText('p', 'primary');
    expect(writeText).not.toHaveBeenCalled();
  });

  it('hands the addon something that is not a Promise', () => {
    // `ClipboardAddon` does `if (result instanceof Promise) return
    // result.then(...)`, and a promise reaching xterm's OSC dispatcher pauses
    // the parser until it settles. A paused parser fires no write callbacks,
    // and those are what release `pendingReplayWrites` — a term in the send
    // gate. Awaiting here would let a program pack the agent's 4 MiB replay
    // ring with OSC 52 writes and freeze both the screen and the keyboard after
    // every reconnect. Asserted on the shape because no behavioural assertion
    // on this unit can see it, and mirroring the addon's own predicate rather
    // than checking `undefined` keeps the two in step.
    const result: unknown = new TerminalClipboardProvider().writeText('c', 'copied');

    expect(result).not.toBeInstanceOf(Promise);
  });
});

describe('isOsc52Read', () => {
  it('matches the query form and not a write', () => {
    expect(isOsc52Read('c;?')).toBe(true);
    expect(isOsc52Read('p;?')).toBe(true);
    expect(isOsc52Read('c;aGVsbG8=')).toBe(false);
    expect(isOsc52Read('c;')).toBe(false);
    expect(isOsc52Read('c')).toBe(false);
  });
});

describe('refuseOsc52Reads', () => {
  it('swallows a query on OSC 52 and lets a write fall through to the addon', () => {
    // Returning true stops xterm's dispatch, so nothing is ever reported back
    // to the PTY; returning false lets the clipboard addon perform the write.
    let registered: { ident?: number; handler?: (data: string) => boolean } = {};
    const terminal = {
      parser: {
        registerOscHandler(ident: number, handler: (data: string) => boolean) {
          registered = { ident, handler };
          return { dispose: vi.fn() };
        },
      },
    } as unknown as Terminal;

    refuseOsc52Reads(terminal);

    expect(registered.ident).toBe(52);
    expect(registered.handler?.('c;?')).toBe(true);
    expect(registered.handler?.('c;aGVsbG8=')).toBe(false);
  });
});

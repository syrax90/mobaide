import type { Terminal } from '@xterm/xterm';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  installOsc52Policy,
  isOsc52Read,
  TerminalClipboardProvider,
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

/** The handler xterm would call, plus the ident it was registered under. */
function installPolicy(isReplaying: () => boolean): {
  ident?: number;
  handler: (data: string) => boolean;
} {
  let registered: { ident?: number; handler?: (data: string) => boolean } = {};
  const terminal = {
    parser: {
      registerOscHandler(ident: number, handler: (data: string) => boolean) {
        registered = { ident, handler };
        return { dispose: vi.fn() };
      },
    },
  } as unknown as Terminal;

  installOsc52Policy(terminal, isReplaying);
  const handler = registered.handler;
  if (handler === undefined) throw new Error('no OSC handler was registered');
  return { ident: registered.ident, handler };
}

describe('installOsc52Policy', () => {
  it('swallows a query on OSC 52 and lets a live write fall through to the addon', () => {
    // Returning true stops xterm's dispatch, so nothing is ever reported back
    // to the PTY; returning false lets the clipboard addon perform the write.
    const { ident, handler } = installPolicy(() => false);

    expect(ident).toBe(52);
    expect(handler('c;?')).toBe(true);
    expect(handler('c;aGVsbG8=')).toBe(false);
  });

  it('swallows a write attributed to replayed history and passes a live one on', () => {
    let replaying = false;
    const { handler } = installPolicy(() => replaying);

    expect(handler('c;aGVsbG8=')).toBe(false);

    replaying = true;

    expect(handler('c;aGVsbG8=')).toBe(true);
  });

  it('reads the flag per dispatch rather than at registration', () => {
    // xterm parses asynchronously, so the span a chunk belongs to is only known
    // when its terminator is dispatched — a value sampled when the handler was
    // installed would attribute the whole session to whatever was true then.
    let replaying = true;
    const { handler } = installPolicy(() => replaying);
    expect(handler('c;aGVsbG8=')).toBe(true);

    replaying = false;

    expect(handler('c;aGVsbG8=')).toBe(false);
  });
});

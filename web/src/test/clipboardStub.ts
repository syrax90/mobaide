import { afterEach, beforeEach } from 'vitest';

/**
 * Install a `navigator.clipboard` stand-in for the enclosing describe.
 *
 * jsdom ships none, so one is defined before each test and removed afterwards.
 * Returns the live array of what was written, emptied before each test — call
 * it in a describe body and keep the result.
 */
function stubClipboard(write: () => Promise<void>): string[] {
  const writes: string[] = [];

  beforeEach(() => {
    writes.length = 0;
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: {
        writeText: (text: string) => {
          writes.push(text);
          return write();
        },
      },
    });
  });

  afterEach(() => {
    Reflect.deleteProperty(navigator, 'clipboard');
  });

  return writes;
}

/** Record what the app puts on the system clipboard. Every write succeeds. */
export function recordingClipboard(): string[] {
  return stubClipboard(() => Promise.resolve());
}

/**
 * The same recorder with every write refused, as Firefox and Safari do when no
 * user gesture is behind one. The attempt is still recorded, so a caller can
 * tell "refused" apart from "never tried".
 */
export function refusingClipboard(): string[] {
  return stubClipboard(() => Promise.reject(new Error('clipboard write refused')));
}

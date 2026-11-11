import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { writeClipboardText } from './clipboard';

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
  it('swallows the refusal Safari and Firefox give a gesture-less write', async () => {
    // Both callers need this: an unhandled rejection would surface inside
    // xterm's parser promise chain for the OSC 52 path, and as an uncaught
    // error in a click handler for the agent-credentials copy buttons.
    writeText.mockRejectedValue(new Error('NotAllowedError'));

    await expect(writeClipboardText('hello')).resolves.toBe(false);
  });

  it('reports failure when the browser exposes no clipboard at all', async () => {
    Reflect.deleteProperty(navigator, 'clipboard');

    await expect(writeClipboardText('hello')).resolves.toBe(false);
  });
});

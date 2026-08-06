import { cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { useAppShortcuts } from './useAppShortcuts';
import type { AppCommand } from './appShortcuts';

/** Real UAs, so `browserEnv`'s sniffing order is exercised, not bypassed. */
const UA = {
  chromeWindows:
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  chromeMac:
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
};

/** Read once per mount, so it has to be in place before `renderHook`. */
function runningOn(userAgent: string): void {
  Object.defineProperty(navigator, 'userAgent', { value: userAgent, configurable: true });
}

let fired: AppCommand[];
let planted: HTMLElement[];

const mount = () =>
  renderHook(() =>
    useAppShortcuts((command) => {
      fired.push(command);
    }),
  );

/** An element outside the hook's own tree, standing in for the terminal grid. */
function plant(): HTMLTextAreaElement {
  const element = document.body.appendChild(document.createElement('textarea'));
  planted.push(element);
  return element;
}

function press(target: EventTarget, init: KeyboardEventInit): KeyboardEvent {
  const event = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
  target.dispatchEvent(event);
  return event;
}

const paletteChord: KeyboardEventInit = { code: 'KeyP', key: 'P', shiftKey: true };

beforeEach(() => {
  fired = [];
  planted = [];
  runningOn(UA.chromeWindows);
});

afterEach(() => {
  cleanup();
  for (const element of planted) element.remove();
  Reflect.deleteProperty(navigator, 'userAgent');
});

describe('useAppShortcuts', () => {
  // The regression the hook exists for: the chord used to be matched inside
  // xterm's key handler, which is registered on the terminal's own textarea, so
  // it was dead whenever focus sat anywhere else — the composer, a bar button,
  // or `document.body`, which is where focus lands after a dialog closes.
  it('answers the chord wherever it is pressed on the page', () => {
    const composer = plant();
    mount();

    press(document.body, { ...paletteChord, ctrlKey: true });
    press(composer, { ...paletteChord, ctrlKey: true });

    expect(fired).toEqual(['commandPalette', 'commandPalette']);
  });

  it('takes the chord away from the browser and from the page below it', () => {
    // Capture on the document plus `stopPropagation`: Ctrl+Shift+P opens a
    // private window in Firefox, and xterm would otherwise also encode it to a
    // CSI-u sequence and send it to the program.
    const grid = plant();
    let reachedGrid = 0;
    grid.addEventListener('keydown', () => {
      reachedGrid += 1;
    });
    mount();

    const event = press(grid, { ...paletteChord, ctrlKey: true });

    expect(event.defaultPrevented).toBe(true);
    expect(reachedGrid).toBe(0);
    expect(fired).toEqual(['commandPalette']);
  });

  it('leaves every other keystroke alone', () => {
    // The listener sits on every keystroke the page sees, so a chord it does
    // not own must reach the terminal untouched.
    mount();

    const event = press(document.body, { code: 'KeyP', key: 'p' });

    expect(event.defaultPrevented).toBe(false);
    expect(fired).toEqual([]);
  });

  it('reads the primary modifier off the platform', () => {
    // Cmd+Shift+P on a Mac, Ctrl+Shift+P everywhere else — which also keeps
    // Ctrl+Shift+P available to the terminal on macOS.
    runningOn(UA.chromeMac);
    mount();

    press(document.body, { ...paletteChord, ctrlKey: true });
    expect(fired).toEqual([]);

    press(document.body, { ...paletteChord, metaKey: true });
    expect(fired).toEqual(['commandPalette']);
  });

  it('stops listening once the screen unmounts', () => {
    // The listener is on the document, not on the terminal, so leaving it
    // behind would open a palette belonging to an unmounted screen.
    mount().unmount();

    const event = press(document.body, { ...paletteChord, ctrlKey: true });

    expect(event.defaultPrevented).toBe(false);
    expect(fired).toEqual([]);
  });
});

import { cleanup, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { useReloadGuard } from './useReloadGuard';

function press(init: KeyboardEventInit, target: EventTarget = document.body): KeyboardEvent {
  const event = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
  target.dispatchEvent(event);
  return event;
}

const mount = (active = true) =>
  renderHook(() => {
    useReloadGuard(active);
  });

afterEach(cleanup);

describe('useReloadGuard', () => {
  // `reservedKeys.ts` marks these three `appSuppressed`, which is the reason
  // the composer tells a user saving Ctrl+R that it will reach the program
  // rather than reloading. That claim is only true because of this hook.
  it('cancels the reload chords the reserved-key table says it cancels', () => {
    mount();

    expect(press({ code: 'KeyR', key: 'r', ctrlKey: true }).defaultPrevented).toBe(true);
    expect(press({ code: 'KeyR', key: 'R', ctrlKey: true, shiftKey: true }).defaultPrevented).toBe(
      true,
    );
    expect(press({ code: 'F5', key: 'F5' }).defaultPrevented).toBe(true);
  });

  it('cancels Cmd+R too, since macOS puts reload there', () => {
    mount();

    expect(press({ code: 'KeyR', key: 'r', metaKey: true }).defaultPrevented).toBe(true);
  });

  it('lets the chord keep travelling, so the program still receives it', () => {
    // `preventDefault` without `stopPropagation` is the whole trick, and it is
    // what makes the composer's "it reaches the program" wording true: the
    // browser does not reload, and xterm still encodes Ctrl+R to `0x12` for
    // readline's reverse history search.
    const grid = document.body.appendChild(document.createElement('textarea'));
    let reachedGrid = 0;
    grid.addEventListener('keydown', () => {
      reachedGrid += 1;
    });
    mount();

    const event = press({ code: 'KeyR', key: 'r', ctrlKey: true }, grid);

    expect(event.defaultPrevented).toBe(true);
    expect(reachedGrid).toBe(1);
    grid.remove();
  });

  it('lets a bare r through, or every word with an r in it would lose letters', () => {
    mount();

    expect(press({ code: 'KeyR', key: 'r' }).defaultPrevented).toBe(false);
  });

  it('lets Alt+R through, which is a chord in its own right and not a reload', () => {
    mount();

    expect(press({ code: 'KeyR', key: 'r', altKey: true }).defaultPrevented).toBe(false);
    expect(
      press({ code: 'KeyR', key: 'r', altKey: true, ctrlKey: true }).defaultPrevented,
    ).toBe(false);
  });

  it('does nothing while inactive', () => {
    mount(false);

    expect(press({ code: 'KeyR', key: 'r', ctrlKey: true }).defaultPrevented).toBe(false);
  });

  it('stops swallowing the chord once the screen unmounts', () => {
    // Left behind, this would break reload for the whole SPA.
    mount().unmount();

    expect(press({ code: 'KeyR', key: 'r', ctrlKey: true }).defaultPrevented).toBe(false);
    expect(press({ code: 'F5', key: 'F5' }).defaultPrevented).toBe(false);
  });
});

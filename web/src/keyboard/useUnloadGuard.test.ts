import { cleanup, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { useUnloadGuard } from './useUnloadGuard';

interface Unload {
  event: Event;
  /** What the handler assigned, or `undefined` if it never touched it. */
  assigned: unknown;
}

/**
 * `returnValue` cannot be read back to find out what was assigned: on an Event,
 * it is the legacy mirror of the canceled flag, so once `preventDefault()` has
 * run the getter answers `false` no matter what was written. Shadowing it with
 * an own accessor is the only way to see the value the handler actually chose,
 * which is the whole difference between arming the prompt and not.
 */
function unload(): Unload {
  const event = new Event('beforeunload', { cancelable: true });
  const state: Unload = { event, assigned: undefined };
  Object.defineProperty(event, 'returnValue', {
    configurable: true,
    get: () => state.assigned,
    set: (value: unknown) => {
      state.assigned = value;
    },
  });
  window.dispatchEvent(event);
  return state;
}

afterEach(cleanup);

describe('useUnloadGuard', () => {
  it('arms the prompt with a truthy value, not the neutral empty string', () => {
    // `returnValue` is *initialised* to `''`, so assigning `''` back arms
    // nothing — the legacy Chrome/Edge path only fires on a truthy value.
    renderHook(() => {
      useUnloadGuard(true);
    });

    const closing = unload();

    expect(closing.event.defaultPrevented).toBe(true);
    expect(closing.assigned).toBeTruthy();
  });

  it('says nothing while the session is not live and running', () => {
    // Reading a finished transcript is not work in progress; nagging on the way
    // out of one would train the user to click through the prompt.
    renderHook(() => {
      useUnloadGuard(false);
    });

    const closing = unload();

    expect(closing.event.defaultPrevented).toBe(false);
    expect(closing.assigned).toBeUndefined();
  });

  it('stops guarding as soon as the session stops running', () => {
    const guard = renderHook(({ active }) => {
      useUnloadGuard(active);
    }, { initialProps: { active: true } });

    guard.rerender({ active: false });

    expect(unload().event.defaultPrevented).toBe(false);
  });

  it('stops guarding once the screen unmounts', () => {
    // The listener is on the window, so leaving it behind would keep prompting
    // after the user has navigated away from the terminal entirely.
    renderHook(() => {
      useUnloadGuard(true);
    }).unmount();

    expect(unload().event.defaultPrevented).toBe(false);
  });
});

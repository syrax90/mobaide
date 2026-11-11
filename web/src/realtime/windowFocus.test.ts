import { afterEach, describe, expect, it } from 'vitest';
import { fakeEventTarget } from './__testing__/fakeEventTarget';
import { observeWindowFocus, onWindowFocus, windowFocused, type FocusWin } from './windowFocus';

/**
 * `fakeEventTarget` speaks `hidden`; `FocusWin` speaks `hasFocus()`. The wrap
 * keeps the shared double while letting each case seed and flip focus.
 */
function fakeWin(focused = true) {
  const target = fakeEventTarget();
  const state = { focused };
  const win: FocusWin = {
    hasFocus: () => state.focused,
    addEventListener: target.addEventListener,
    removeEventListener: target.removeEventListener,
  };
  return {
    win,
    blur: () => {
      state.focused = false;
      target.fire('blur');
    },
    focus: () => {
      state.focused = true;
      target.fire('focus');
    },
    fire: target.fire,
    count: target.count,
  };
}

const uninstallers: (() => void)[] = [];

function install(win: FocusWin): void {
  uninstallers.push(observeWindowFocus(win));
}

afterEach(() => {
  for (const uninstall of uninstallers.splice(0)) uninstall();
});

describe('observeWindowFocus', () => {

  it('seeds from a window that was already blurred at installation', () => {
    const w = fakeWin(false);
    install(w.win);

    expect(windowFocused()).toBe(false);
  });

  it('notifies subscribers on both transitions', () => {
    const w = fakeWin();
    install(w.win);
    const seen: boolean[] = [];
    onWindowFocus((focused) => seen.push(focused));

    w.blur();
    w.focus();

    expect(seen).toEqual([false, true]);
  });

  it('removes the listeners and reverts to focused on teardown', () => {
    // Reverting matters: a torn-down installation must not strand subscribers
    // believing the window is blurred, and a test file must not leak one
    // case's blurred window into the next.
    const w = fakeWin(false);
    const uninstall = observeWindowFocus(w.win);

    expect(windowFocused()).toBe(false);

    uninstall();

    expect(w.count('focus')).toBe(0);
    expect(w.count('blur')).toBe(0);
    expect(windowFocused()).toBe(true);
  });

  it('keeps subscribers registered across an uninstall and re-install', () => {
    // StrictMode runs the installing effect as install -> uninstall -> install,
    // and a model that subscribed in between has to survive it.
    const first = fakeWin();
    const uninstall = observeWindowFocus(first.win);
    const seen: boolean[] = [];
    onWindowFocus((focused) => seen.push(focused));
    uninstall();

    const second = fakeWin();
    install(second.win);
    second.blur();

    expect(seen).toEqual([false]);
  });
});

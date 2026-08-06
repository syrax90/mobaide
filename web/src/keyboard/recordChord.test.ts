import { describe, expect, it } from 'vitest';
import {
  recordChordFromEvent,
  unsupportedMessage,
  type RecordableKeyEvent,
} from './recordChord';

const press = (code: string, over: Partial<RecordableKeyEvent> = {}): RecordableKeyEvent => ({
  type: 'keydown',
  code,
  ctrlKey: false,
  altKey: false,
  shiftKey: false,
  metaKey: false,
  ...over,
});

/** Only `AltGraph` is ever asked about, so the stub answers that one query. */
const withAltGraph = (over: Partial<RecordableKeyEvent> = {}): Partial<RecordableKeyEvent> => ({
  getModifierState: (key: string) => key === 'AltGraph',
  ...over,
});

describe('recordChordFromEvent — recorded chords', () => {
  it('records the modifiers and the physical key', () => {
    expect(recordChordFromEvent(press('KeyI', { ctrlKey: true, shiftKey: true }))).toEqual({
      status: 'chord',
      chord: { ctrl: true, alt: false, shift: true, key: 'i' },
    });
  });

  it('records an unmodified key', () => {
    expect(recordChordFromEvent(press('F5'))).toEqual({
      status: 'chord',
      chord: { ctrl: false, alt: false, shift: false, key: 'f5' },
    });
  });

  it('lowercases the letter, so Shift never changes which key was recorded', () => {
    // `code` is `KeyA` whether or not Shift is down; a `key`-based recorder
    // would store `A` here and `a` without Shift, and the token grammar is
    // lowercase-only.
    const outcome = recordChordFromEvent(press('KeyA', { shiftKey: true }));

    expect(outcome).toEqual({
      status: 'chord',
      chord: { ctrl: false, alt: false, shift: true, key: 'a' },
    });
  });

  it('maps the numpad onto the same base keys as the top row', () => {
    // The PTY cannot tell the two apart — the byte is identical — so recording
    // `Numpad7` as its own key would produce a token that never matches.
    expect(recordChordFromEvent(press('Numpad7', { ctrlKey: true }))).toEqual({
      status: 'chord',
      chord: { ctrl: true, alt: false, shift: false, key: '7' },
    });
    expect(recordChordFromEvent(press('NumpadEnter', { altKey: true }))).toEqual({
      status: 'chord',
      chord: { ctrl: false, alt: true, shift: false, key: 'enter' },
    });
  });

  it('records the four named specials', () => {
    const keys = ['Tab', 'Enter', 'Space', 'Backspace'].map(
      (code) => recordChordFromEvent(press(code, { ctrlKey: true })),
    );

    expect(keys).toEqual([
      { status: 'chord', chord: { ctrl: true, alt: false, shift: false, key: 'tab' } },
      { status: 'chord', chord: { ctrl: true, alt: false, shift: false, key: 'enter' } },
      { status: 'chord', chord: { ctrl: true, alt: false, shift: false, key: 'space' } },
      { status: 'chord', chord: { ctrl: true, alt: false, shift: false, key: 'backspace' } },
    ]);
  });

  it('accepts exactly two modifiers', () => {
    expect(recordChordFromEvent(press('KeyZ', { ctrlKey: true, altKey: true }))).toEqual({
      status: 'chord',
      chord: { ctrl: true, alt: true, shift: false, key: 'z' },
    });
  });
});

describe('recordChordFromEvent — presses the recorder stays silent about', () => {
  it('ignores anything that is not a keydown', () => {
    expect(recordChordFromEvent(press('KeyA', { type: 'keyup', ctrlKey: true }))).toEqual({
      status: 'ignored',
      reason: 'notKeydown',
    });
  });

  it('ignores a press an IME is composing with', () => {
    expect(recordChordFromEvent(press('KeyA', { isComposing: true }))).toEqual({
      status: 'ignored',
      reason: 'ime',
    });
  });

  it('ignores the legacy keyCode 229 an IME sends without isComposing', () => {
    // Safari and older Chromium report only the sentinel; reading `code` there
    // records the physical key behind a candidate selection.
    expect(recordChordFromEvent(press('KeyA', { keyCode: 229 }))).toEqual({
      status: 'ignored',
      reason: 'ime',
    });
  });

  it('ignores AltGr, which is indistinguishable from Ctrl+Alt on the flags alone', () => {
    // A German user typing `@` presses AltGr+Q, and Windows reports it as
    // Ctrl+Alt — recording it would save a chord they never chose.
    expect(
      recordChordFromEvent(
        press('KeyQ', { ctrlKey: true, altKey: true, ...withAltGraph() }),
      ),
    ).toEqual({ status: 'ignored', reason: 'altGraph' });
  });

  it('records a real Ctrl+Alt chord when AltGraph is not standing', () => {
    expect(
      recordChordFromEvent(
        press('KeyQ', {
          ctrlKey: true,
          altKey: true,
          getModifierState: () => false,
        }),
      ),
    ).toEqual({
      status: 'chord',
      chord: { ctrl: true, alt: true, shift: false, key: 'q' },
    });
  });

  it('ignores a modifier held on its own while the user builds a chord', () => {
    for (const code of ['ControlLeft', 'ShiftRight', 'AltLeft', 'CapsLock', 'Fn']) {
      expect(recordChordFromEvent(press(code, { ctrlKey: true }))).toEqual({
        status: 'ignored',
        reason: 'modifierOnly',
      });
    }
  });

  it('ignores a bare Cmd press rather than calling it unsupported', () => {
    // Cmd arrives before the key it modifies, so treating the modifier itself
    // as a rejected chord would flash an error every time someone reaches for
    // one — the rejection belongs on the completed press.
    expect(recordChordFromEvent(press('MetaLeft', { metaKey: true }))).toEqual({
      status: 'ignored',
      reason: 'modifierOnly',
    });
  });
});

describe('recordChordFromEvent — presses the contract cannot carry', () => {
  it('refuses Cmd and the Windows key, which the shared grammar has no room for', () => {
    expect(recordChordFromEvent(press('KeyP', { metaKey: true, shiftKey: true }))).toEqual({
      status: 'unsupported',
      reason: 'metaKey',
      code: 'KeyP',
    });
  });

  it('refuses Escape', () => {
    expect(recordChordFromEvent(press('Escape'))).toEqual({
      status: 'unsupported',
      reason: 'escape',
      code: 'Escape',
    });
  });

  it('names Escape as the reason even when the press also breaks the modifier cap', () => {
    // Ctrl+Alt+Shift+Esc is both; "Esc has its own button" is the answer that
    // tells the user what to do instead.
    expect(
      recordChordFromEvent(press('Escape', { ctrlKey: true, altKey: true, shiftKey: true })),
    ).toEqual({ status: 'unsupported', reason: 'escape', code: 'Escape' });
  });

  it('refuses three modifiers', () => {
    expect(
      recordChordFromEvent(press('KeyA', { ctrlKey: true, altKey: true, shiftKey: true })),
    ).toEqual({ status: 'unsupported', reason: 'tooManyModifiers', code: 'KeyA' });
  });

  it('refuses a base key outside the shared domain', () => {
    // Web can never sync a token the two mobile clients cannot render, so these
    // are refused at the recorder rather than saved and dropped later.
    for (const code of ['BracketLeft', 'ArrowUp', 'F13', 'IntlBackslash', 'NumpadAdd']) {
      expect(recordChordFromEvent(press(code, { ctrlKey: true }))).toEqual({
        status: 'unsupported',
        reason: 'unknownKey',
        code,
      });
    }
  });
});

describe('unsupportedMessage', () => {
  it('names the key the user actually pressed', () => {
    expect(unsupportedMessage({ reason: 'unknownKey', code: 'BracketLeft' })).toContain(
      'BracketLeft',
    );
  });
});

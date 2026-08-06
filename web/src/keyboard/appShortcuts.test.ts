import { describe, expect, it } from 'vitest';
import {
  appShortcutFor,
  appShortcutTokens,
  matchAppShortcut,
  PALETTE_CHORD_CODE,
  PALETTE_CHORD_KEY,
  type AppShortcutEvent,
} from './appShortcuts';
import type { KeyboardEnv } from './browserEnv';

const env = (over: Partial<KeyboardEnv> = {}): KeyboardEnv => ({
  browser: 'chromium',
  os: 'windows',
  standalone: false,
  ...over,
});

const press = (over: Partial<AppShortcutEvent> = {}): AppShortcutEvent => ({
  type: 'keydown',
  code: 'KeyP',
  ctrlKey: false,
  altKey: false,
  shiftKey: false,
  metaKey: false,
  ...over,
});

describe('matchAppShortcut', () => {
  it('claims Ctrl+Shift+P off a PC', () => {
    expect(matchAppShortcut(press({ ctrlKey: true, shiftKey: true }), env())).toBe(
      'commandPalette',
    );
    expect(matchAppShortcut(press({ ctrlKey: true, shiftKey: true }), env({ os: 'linux' }))).toBe(
      'commandPalette',
    );
  });

  it('claims Cmd+Shift+P on a Mac', () => {
    expect(matchAppShortcut(press({ metaKey: true, shiftKey: true }), env({ os: 'mac' }))).toBe(
      'commandPalette',
    );
  });

  // The whole point of moving to Cmd there: Ctrl+Shift+P stays a chord the
  // terminal can receive on macOS, so the app must not answer to it.
  it('leaves Ctrl+Shift+P to the terminal on a Mac', () => {
    expect(matchAppShortcut(press({ ctrlKey: true, shiftKey: true }), env({ os: 'mac' }))).toBeNull();
  });

  it('ignores Cmd+Shift+P off a Mac, where Cmd is not the primary modifier', () => {
    expect(matchAppShortcut(press({ metaKey: true, shiftKey: true }), env())).toBeNull();
  });

  it('ignores a press that holds both primary modifiers at once', () => {
    // Ctrl+Cmd+Shift+P is a different chord on both platforms, and answering to
    // it would steal a third chord nobody asked for.
    expect(
      matchAppShortcut(press({ ctrlKey: true, metaKey: true, shiftKey: true }), env({ os: 'mac' })),
    ).toBeNull();
    expect(
      matchAppShortcut(press({ ctrlKey: true, metaKey: true, shiftKey: true }), env()),
    ).toBeNull();
  });

  it('requires Shift and refuses Alt', () => {
    expect(matchAppShortcut(press({ ctrlKey: true }), env())).toBeNull();
    expect(
      matchAppShortcut(press({ ctrlKey: true, shiftKey: true, altKey: true }), env()),
    ).toBeNull();
  });

  it('matches the physical key, so a remapped layout finds the palette in the same place', () => {
    // `code`, not `key`: on AZERTY the letter printed on this key differs, and
    // on Dvorak `KeyP` sits elsewhere entirely.
    expect(matchAppShortcut(press({ code: 'KeyR', ctrlKey: true, shiftKey: true }), env())).toBeNull();
  });

  it('ignores keyup, so the palette opens once per press', () => {
    expect(
      matchAppShortcut(press({ type: 'keyup', ctrlKey: true, shiftKey: true }), env()),
    ).toBeNull();
  });
});

describe('the palette chord definition', () => {
  // Everything else — the token the conflict checker compares, the hint the
  // button shows — is derived from the one chord. The physical `code` the
  // matcher tests is the last pair still written out by hand, so moving the
  // chord to another letter and forgetting the code has to fail here rather
  // than silently leave the palette answering to the old key.
  it('keeps the physical code in step with the chord it is meant to be', () => {
    // Holds while the chord sits on a letter; moving it to a digit or an F-key
    // should fail here and be looked at rather than quietly re-derived.
    expect(PALETTE_CHORD_CODE).toBe(`Key${PALETTE_CHORD_KEY.toUpperCase()}`);
  });

  it('hints the chord it actually matches, per platform', () => {
    // Asserted against the chord rather than against a literal, because the Mac
    // hint is a hand-written glyph string — ⌘ is outside the shared grammar —
    // and is exactly the thing that would be left behind if the chord moved.
    const palette = appShortcutFor('commandPalette');
    const letter = PALETTE_CHORD_KEY.toUpperCase();

    expect(palette?.label).toBe('Command palette');
    expect(palette?.hint('windows')).toBe(`Ctrl+Shift+${letter}`);
    expect(palette?.hint('linux')).toBe(`Ctrl+Shift+${letter}`);
    expect(palette?.hint('mac')).toBe(`⌘⇧${letter}`);
  });
});

describe('appShortcutTokens', () => {
  it('reports the app-owned chord on a PC', () => {
    expect(appShortcutTokens(env())).toEqual(['ctrl+shift+p']);
    expect(appShortcutTokens(env({ os: 'linux' }))).toEqual(['ctrl+shift+p']);
  });

  // On macOS the palette lives on Cmd, which the shared token grammar cannot
  // even express — so the composer must not warn that Ctrl+Shift+P is taken.
  it('reports nothing on a Mac, where the app takes no grammar-expressible chord', () => {
    expect(appShortcutTokens(env({ os: 'mac' }))).toEqual([]);
  });
});

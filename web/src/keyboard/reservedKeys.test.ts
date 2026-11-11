import { describe, expect, it } from 'vitest';
import { chordToken, parseChordToken } from '../protocol/keyChord';
import type { KeyboardEnv } from './browserEnv';
import { lookupReservedKey, RESERVED_KEYS } from './reservedKeys';

const env = (over: Partial<KeyboardEnv> = {}): KeyboardEnv => ({
  browser: 'chromium',
  os: 'windows',
  standalone: false,
  ...over,
});

describe('RESERVED_KEYS table', () => {
  // A row whose token is not in canonical form can never match: the recorder
  // and the repository both produce `chordToken()` output, so `shift+ctrl+w`
  // would sit in the table warning nobody, forever and silently.
  it('spells every token in the canonical form the recorder produces', () => {
    for (const entry of RESERVED_KEYS) {
      const chord = parseChordToken(entry.token);
      expect(chord, `${entry.token} is not a valid chord token`).not.toBeNull();
      if (chord !== null) expect(chordToken(chord)).toBe(entry.token);
    }
  });
});

describe('lookupReservedKey — matching', () => {
  it('finds a row for the browser and OS it was written for', () => {
    const found = lookupReservedKey('ctrl+w', env({ browser: 'firefox' }));

    expect(found.entries).toHaveLength(1);
    expect(found.entries[0]?.browserAction).toBe('Close tab');
  });

  it('does not apply a Firefox row to Chromium', () => {
    // Both browsers close the tab, but only Firefox's row carries the WONTFIX
    // citation that makes it unstoppable — the two must not be merged.
    expect(lookupReservedKey('ctrl+w', env()).entries).toHaveLength(1);
    expect(lookupReservedKey('ctrl+w', env()).blocked).toBe(false);
  });

  it('does not apply a PC row on macOS, where the chord sits on Cmd', () => {
    expect(lookupReservedKey('ctrl+w', env({ os: 'mac' })).entries).toEqual([]);
    expect(lookupReservedKey('ctrl+t', env({ browser: 'firefox', os: 'mac' })).entries).toEqual([]);
  });

  it('applies a desktop-wide row on every desktop OS', () => {
    // Ctrl+Tab is one of the few browser chords Apple did not move to Cmd.
    for (const os of ['mac', 'windows', 'linux'] as const) {
      expect(lookupReservedKey('ctrl+tab', env({ os })).entries).toHaveLength(1);
    }
  });

  it('reports nothing for a chord no browser claims', () => {
    expect(lookupReservedKey('ctrl+y', env())).toEqual({
      entries: [],
      blocked: false,
      relaxed: false,
      appSuppressed: false,
    });
  });
});

describe('lookupReservedKey — blocked', () => {
  it('blocks the Firefox chords Mozilla closed WONTFIX', () => {
    for (const token of ['ctrl+w', 'ctrl+t', 'ctrl+n']) {
      expect(lookupReservedKey(token, env({ browser: 'firefox' })).blocked).toBe(true);
    }
  });

  it('blocks the bare F-keys macOS spends on brightness and volume', () => {
    expect(lookupReservedKey('f5', env({ browser: 'safari', os: 'mac' })).blocked).toBe(true);
    expect(lookupReservedKey('f5', env({ browser: 'chromium', os: 'mac' })).blocked).toBe(true);
  });

  it('blocks the window-manager chords the browser never even sees', () => {
    expect(lookupReservedKey('alt+f4', env()).blocked).toBe(true);
    expect(lookupReservedKey('alt+tab', env({ os: 'linux' })).blocked).toBe(true);
  });
});

describe('lookupReservedKey — app-suppressed', () => {
  it('reports the reload chords the app cancels itself', () => {
    for (const token of ['ctrl+r', 'ctrl+shift+r', 'f5']) {
      const found = lookupReservedKey(token, env());

      expect(found.appSuppressed, token).toBe(true);
      expect(found.entries.length, token).toBeGreaterThan(0);
    }
  });

  it('keeps claiming nothing about whether the browser would have released it', () => {
    // `useReloadGuard` cancelling the chord says nothing about
    // `preventDefault()` being honoured in general, which is what
    // `pageCanCancel` records — and nobody has measured that for reload.
    const found = lookupReservedKey('ctrl+r', env());

    expect(found.entries.every((entry) => entry.pageCanCancel === 'unknown')).toBe(true);
    expect(found.blocked).toBe(false);
  });

  it('withholds the flag when something the app cannot cancel also claims the chord', () => {
    // F5 on a Mac is a hardware brightness key before it is anything else, so
    // the page never sees it — the guard cannot suppress what never arrives.
    // Hence `every`, not `some`.
    const found = lookupReservedKey('f5', env({ os: 'mac' }));

    expect(found.entries.length).toBeGreaterThan(0);
    expect(found.appSuppressed).toBe(false);
    expect(found.blocked).toBe(true);
  });
});

describe('lookupReservedKey — standalone relaxation', () => {
  it('relaxes a chord flagged as released by an installed window', () => {
    const found = lookupReservedKey('ctrl+t', env({ standalone: true }));

    expect(found.relaxed).toBe(true);
    // Still reported: relaxed softens the warning, it never removes it.
    expect(found.entries).toHaveLength(1);
    expect(found.blocked).toBe(false);
  });

  it('does not relax a row that carries no standalone flag', () => {
    // Incognito has no PWA analogue, so nothing was ever claimed about it.
    const found = lookupReservedKey('ctrl+shift+n', env({ standalone: true }));

    expect(found.entries).toHaveLength(1);
    expect(found.relaxed).toBe(false);
  });

  it('keeps an unsuppressable chord blocked even in an installed window', () => {
    // Firefox's Ctrl+W is not flagged relaxable, so installing the app must not
    // downgrade "will not release it" to "may still pass it through".
    const found = lookupReservedKey('ctrl+w', env({ browser: 'firefox', standalone: true }));

    expect(found.blocked).toBe(true);
    expect(found.relaxed).toBe(false);
  });
});

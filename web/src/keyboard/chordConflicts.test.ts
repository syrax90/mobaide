import { describe, expect, it } from 'vitest';
import { chordToken, parseChordToken, type KeyChord } from '../protocol/keyChord';
import { controlKeysFor } from '../terminal/controlKeys';
import type { KeyboardEnv } from './browserEnv';
import { classifyChordConflicts, type ChordConflict, type ConflictInput } from './chordConflicts';

/**
 * `browser: 'unknown'` matches no row in the reserved table, so a test that is
 * not about browser reservations sees only the class it asked for.
 */
const env = (over: Partial<KeyboardEnv> = {}): KeyboardEnv => ({
  browser: 'unknown',
  os: 'windows',
  standalone: false,
  ...over,
});

function chordOf(token: string): KeyChord {
  const chord = parseChordToken(token);
  if (chord === null) throw new Error(`${token} is not a chord token`);
  return chord;
}

function classify(token: string, over: Partial<Omit<ConflictInput, 'chord' | 'token'>> = {}) {
  const chord = chordOf(token);
  return classifyChordConflicts({
    chord,
    token: chordToken(chord),
    savedTokens: [],
    builtInKeys: controlKeysFor('claude'),
    appTokens: [],
    env: env(),
    ...over,
  });
}

const kinds = (conflicts: ChordConflict[]): string[] => conflicts.map((entry) => entry.kind);

describe('classifyChordConflicts', () => {
  it('reports nothing for a chord nothing else claims', () => {
    expect(classify('ctrl+alt+y')).toEqual([]);
  });
});

describe('classifyChordConflicts — duplicate', () => {
  it('flags a chord already saved in this kind bucket', () => {
    const conflicts = classify('ctrl+shift+i', { savedTokens: ['ctrl+shift+i'] });

    expect(kinds(conflicts)).toEqual(['duplicate']);
    expect(conflicts[0]?.severity).toBe('info');
    expect(conflicts[0]?.message).toContain('Ctrl+Shift+I');
  });

  it('does not flag a chord saved under a different kind', () => {
    expect(classify('ctrl+shift+i', { savedTokens: ['ctrl+shift+j'] })).toEqual([]);
  });
});

describe('classifyChordConflicts — shadows a built-in', () => {
  it('flags a chord that sends the same byte as a built-in button', () => {
    const conflicts = classify('ctrl+c');

    expect(kinds(conflicts)).toEqual(['shadowsBuiltIn']);
    expect(conflicts[0]?.message).toContain('Ctrl+C');
  });

  it('flags Shift+Tab against Mode, which no label comparison would catch', () => {
    // Both encode to `CSI Z`. The button is called "Mode" and the chord is
    // called "Shift+Tab", so only the bytes make the match.
    const conflicts = classify('shift+tab');

    expect(kinds(conflicts)).toEqual(['shadowsBuiltIn']);
    expect(conflicts[0]?.message).toContain('Mode');
  });

  it('flags Ctrl+I against Tab, which collapse onto 0x09', () => {
    const conflicts = classify('ctrl+i');

    expect(kinds(conflicts)).toEqual(['shadowsBuiltIn']);
    expect(conflicts[0]?.message).toContain('Tab');
  });

  it('compares against the kind actually on screen, not a fixed table', () => {
    // Ctrl+L is a built-in for Claude and deliberately absent from the neutral
    // set a shell gets, so the same chord is a conflict in one bucket and a
    // perfectly good shortcut in the other.
    expect(kinds(classify('ctrl+l', { builtInKeys: controlKeysFor('claude') }))).toEqual([
      'shadowsBuiltIn',
    ]);
    expect(classify('ctrl+l', { builtInKeys: controlKeysFor('shell') })).toEqual([]);
  });

  it('flags a chord against a button only that kind renders', () => {
    // Ctrl+K is `0x0B`, Antigravity's fast-approve button; Claude's bar has no
    // key on that byte, so the same chord is free to save there.
    expect(kinds(classify('ctrl+k', { builtInKeys: controlKeysFor('antigravity') }))).toEqual([
      'shadowsBuiltIn',
    ]);
    expect(classify('ctrl+k', { builtInKeys: controlKeysFor('claude') })).toEqual([]);
  });
});

describe('classifyChordConflicts — shadows an app shortcut', () => {
  it('warns that the app will keep the chord instead of the terminal', () => {
    const conflicts = classify('ctrl+shift+p', { appTokens: ['ctrl+shift+p'] });

    expect(kinds(conflicts)).toEqual(['shadowsApp']);
    expect(conflicts[0]?.severity).toBe('warning');
  });

  it('says nothing on a platform where the app claims no such chord', () => {
    // `appShortcutTokens` is empty on macOS; the same chord is free there.
    expect(classify('ctrl+shift+p', { appTokens: [] })).toEqual([]);
  });
});

describe('classifyChordConflicts — browser reserved', () => {
  it('warns that an unsuppressable chord will not reach the terminal at all', () => {
    const conflicts = classify('ctrl+w', { env: env({ browser: 'firefox' }) });

    expect(kinds(conflicts)).toEqual(['browserReserved']);
    expect(conflicts[0]?.severity).toBe('warning');
    expect(conflicts[0]?.message).toContain('will not release it');
    expect(conflicts[0]?.message).toContain('Close tab');
  });

  it('hedges for a chord nobody has measured', () => {
    const conflicts = classify('ctrl+w', { env: env({ browser: 'chromium' }) });

    expect(conflicts[0]?.severity).toBe('warning');
    expect(conflicts[0]?.message).toContain('may be taken');
  });

  it('softens to advisory in an installed window that may hand the chord back', () => {
    const conflicts = classify('ctrl+t', {
      env: env({ browser: 'chromium', standalone: true }),
    });

    expect(kinds(conflicts)).toEqual(['browserReserved']);
    expect(conflicts[0]?.severity).toBe('info');
    expect(conflicts[0]?.message).toContain('may still pass it through');
  });
});

describe('classifyChordConflicts — a chord the app suppresses itself', () => {
  // The neutral shell set, so the reservation is the only class in play —
  // Ctrl+R is also a built-in button on every Claude-shaped kind.
  const reload = () =>
    classify('ctrl+r', {
      builtInKeys: controlKeysFor('shell'),
      env: env({ browser: 'chromium' }),
    });

  it('reassures rather than warns about Ctrl+R, which the terminal keeps', () => {
    // Reverse-i-search is a chord people press on purpose. Telling them the
    // browser may take it is exactly backwards: `useReloadGuard` cancels the
    // reload, so it reaches the program.
    const conflicts = reload();

    expect(kinds(conflicts)).toEqual(['browserReserved']);
    expect(conflicts[0]?.severity).toBe('info');
    expect(conflicts[0]?.message).toContain('the terminal suppresses that');
    expect(conflicts[0]?.message).not.toContain('may be taken');
  });

  it('still names the browser action, so the user knows which chord this is', () => {
    expect(reload()[0]?.message).toContain('Reload');
  });

  it('goes back to warning where the app cannot suppress it', () => {
    // On a Mac, F5 is a hardware key the page never receives, so the guard is
    // irrelevant and the honest answer is that the chord is lost.
    const conflicts = classify('f5', { env: env({ browser: 'chromium', os: 'mac' }) });

    expect(conflicts[0]?.severity).toBe('warning');
    expect(conflicts[0]?.message).toContain('will not release it');
  });
});

describe('classifyChordConflicts — several classes at once', () => {
  it('reports every claim on the chord rather than the first one', () => {
    // Ctrl+Shift+P is the palette chord *and* Firefox's private window: the
    // user needs both facts to understand why typing it does nothing.
    const conflicts = classify('ctrl+shift+p', {
      appTokens: ['ctrl+shift+p'],
      env: env({ browser: 'firefox' }),
    });

    expect(kinds(conflicts)).toEqual(['shadowsApp', 'browserReserved']);
  });

  it('stacks a duplicate on top of a built-in shadow', () => {
    expect(kinds(classify('ctrl+c', { savedTokens: ['ctrl+c'] }))).toEqual([
      'duplicate',
      'shadowsBuiltIn',
    ]);
  });
});

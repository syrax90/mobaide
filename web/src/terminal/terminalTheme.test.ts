import type { ITheme } from '@xterm/xterm';
import { afterEach, describe, expect, it } from 'vitest';
import { terminalTheme } from './terminalTheme';

/**
 * `MobaideColors.ansiColors` — the table Android and iOS render SGR 30-37 and
 * 90-97 with, restated here as the web arm of that three-client contract.
 *
 * Restated rather than derived from a golden because there is no golden that
 * covers it: `tests/protocol_fixtures/ansi/ansi_sgr.json` documents only three
 * of the sixteen (SGR 31, 91 and 32), and the fixture registry defers it to
 * WEB-10's auto-scan log view. So nothing else in the repo can notice a client
 * rendering a different colour for the same byte.
 *
 * A slot left out is not a near miss either: xterm 6 defaults these to the
 * *Tango* palette (`#2e3436`, `#cc0000`, `#4e9a06`, …), a different set
 * entirely, so inheriting instead of spelling them out silently forks the
 * colours.
 */
const MOBAIDE_ANSI = {
  black: '#000000',
  red: '#cd3131',
  green: '#0dbc79',
  yellow: '#e5e510',
  blue: '#2472c8',
  magenta: '#bc3fbc',
  cyan: '#11a8cd',
  white: '#e5e5e5',
  brightBlack: '#666666',
  brightRed: '#f14c4c',
  brightGreen: '#23d18b',
  brightYellow: '#f5f543',
  brightBlue: '#3b8eea',
  brightMagenta: '#d670d6',
  brightCyan: '#29b8db',
  brightWhite: '#ffffff',
} satisfies Partial<ITheme>;

type AnsiSlot = keyof typeof MOBAIDE_ANSI;

const ANSI_SLOTS = Object.keys(MOBAIDE_ANSI) as AnsiSlot[];

const ansiSlotsOf = (theme: ITheme): Record<string, string | undefined> =>
  Object.fromEntries(ANSI_SLOTS.map((slot) => [slot, theme[slot]]));

describe('terminalTheme', () => {
  afterEach(() => {
    const root = document.documentElement;
    delete root.dataset.theme;
    root.removeAttribute('style');
  });

  it('spells out all sixteen ANSI slots with the palette the mobile clients use', () => {
    expect(ansiSlotsOf(terminalTheme())).toEqual(MOBAIDE_ANSI);
  });

  it('returns the same palette whatever the app theme resolves to', () => {
    // The preference defaults to `system`, so this used to be read off
    // `--color-bg`/`--color-fg`: a light-mode OS handed the dark ANSI palette a
    // near-white background. Every terminal emulator, and both mobile clients,
    // pin the grid dark instead.
    const root = document.documentElement;
    root.dataset.theme = 'dark';
    const underDark = terminalTheme();

    root.dataset.theme = 'light';
    root.style.setProperty('--color-bg', '#f7f8fa');
    root.style.setProperty('--color-fg', '#12141a');
    root.style.setProperty('--color-accent', '#1b6ac9');

    expect(terminalTheme()).toEqual(underDark);
  });
});

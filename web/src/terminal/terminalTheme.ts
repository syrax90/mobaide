/**
 * Terminal palette.
 *
 * **The grid is always dark, regardless of the app's light/dark preference**,
 * which is what both mobile clients do and what every terminal emulator does.
 * The reason is not taste: an ANSI palette is a matched set with its
 * background. Pairing a dark 16-colour palette with the light app background
 * (`--color-bg: #f7f8fa`) puts white on near-white — xterm's own default white
 * against it is about 1.4:1, bright white about 1.1:1 — and Claude Code prints
 * plenty of both. Since the theme preference defaults to `system`, that would
 * have been the default experience for every user on a light-mode OS.
 *
 * The 16 colours are `MobaideColors.ansiColors`, the table Android and iOS
 * render with, so a red is the same red on all three clients. They are spelled
 * out rather than inherited: xterm 6's built-in default is the **Tango**
 * palette (`#2e3436`, `#cc0000`, `#4e9a06`, …), a different set entirely — an
 * earlier revision of this file claimed the two already agreed, and they do
 * not. `tests/protocol_fixtures/ansi/ansi_sgr.json` cross-checks three of the
 * sixteen (`31`, `91`, `1;32` → red, brightRed, green); the rest have no golden
 * anywhere in the repo, which is why they are asserted as literals.
 */
import type { ITheme } from '@xterm/xterm';

/**
 * `MobaideColors.ansiColors` — the VS Code dark palette, mirrored by
 * `AnsiParser.basePalette` on iOS and its Android twin.
 */
const ANSI: Pick<
  ITheme,
  | 'black'
  | 'red'
  | 'green'
  | 'yellow'
  | 'blue'
  | 'magenta'
  | 'cyan'
  | 'white'
  | 'brightBlack'
  | 'brightRed'
  | 'brightGreen'
  | 'brightYellow'
  | 'brightBlue'
  | 'brightMagenta'
  | 'brightCyan'
  | 'brightWhite'
> = {
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
};

/** The dark chrome the palette is matched to — the app's own dark tokens. */
const BACKGROUND = '#12141a';
const FOREGROUND = '#e6e8ee';
const CURSOR = '#4da3ff';

export function terminalTheme(): ITheme {
  return {
    ...ANSI,
    background: BACKGROUND,
    foreground: FOREGROUND,
    cursor: CURSOR,
    cursorAccent: BACKGROUND,
    // Alpha rather than a solid fill: xterm composites the selection over the
    // glyphs, and an opaque colour hides the text being selected.
    selectionBackground: 'rgba(77, 163, 255, 0.35)',
    selectionInactiveBackground: 'rgba(122, 134, 154, 0.3)',
  };
}

/**
 * Terminal palette, derived from the app's CSS custom properties.
 *
 * Only the chrome colours are set. The 16 ANSI entries are deliberately left at
 * xterm's defaults, which are already the VS Code dark palette — byte-identical
 * to `MobaideColors.ansiColors`, the table both mobile clients render with and
 * `tests/protocol_fixtures/ansi/ansi_sgr.json` documents. Restating them here
 * would be a second source of truth for colours that already agree.
 */
import type { ITheme } from '@xterm/xterm';

const FALLBACK: Required<Pick<ITheme, 'background' | 'foreground' | 'cursor'>> = {
  background: '#12141a',
  foreground: '#e6e8ee',
  cursor: '#4da3ff',
};

function cssVar(styles: CSSStyleDeclaration, name: string, fallback: string): string {
  const value = styles.getPropertyValue(name).trim();
  return value === '' ? fallback : value;
}

/**
 * Read the live theme off `document.documentElement`, where `ThemeProvider`
 * stamps `data-theme`. Call again after the preference changes — xterm applies a
 * new `options.theme` immediately.
 */
export function readTerminalTheme(root: HTMLElement): ITheme {
  const styles = getComputedStyle(root);
  const accent = cssVar(styles, '--color-accent', FALLBACK.cursor);
  return {
    background: cssVar(styles, '--color-bg', FALLBACK.background),
    foreground: cssVar(styles, '--color-fg', FALLBACK.foreground),
    cursor: accent,
    cursorAccent: cssVar(styles, '--color-bg', FALLBACK.background),
    // Alpha rather than a solid fill: xterm composites the selection over the
    // glyphs, and an opaque colour hides the text being selected.
    selectionBackground: 'rgba(77, 163, 255, 0.35)',
    selectionInactiveBackground: 'rgba(122, 134, 154, 0.3)',
  };
}

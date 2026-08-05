/**
 * SGR (`ESC[…m`) → styled runs, for the Auto Scan log pane.
 *
 * Port of iOS `AnsiParser.parseRuns` (`ios/Mobaide/UI/AutoScan/AnsiParser.swift`),
 * pinned by the shared golden `tests/protocol_fixtures/ansi/ansi_sgr.json`. That
 * fixture asserts integer `[r, g, b]` channels, which is why colours stay
 * framework-free here and become CSS only at render time — and why an
 * HTML-emitting library (`ansi_up`) could not be used: there would be nothing of
 * the right shape to assert.
 *
 * A deliberate superset of Android's parser, which handles only foreground
 * 30-37/90-97 and 256-indices 0-15 (tracked in CLA-250).
 *
 * The terminal screen has no parser of its own — xterm.js owns SGR there — so
 * this is the only consumer.
 */

/** Integer RGB, 0-255 per channel. */
export interface AnsiRgb {
  r: number;
  g: number;
  b: number;
}

/** A maximal run of text sharing one SGR style. */
export interface AnsiRun {
  text: string;
  foreground?: AnsiRgb;
  background?: AnsiRgb;
  bold: boolean;
  italic: boolean;
  underline: boolean;
}

function rgb(hex: number): AnsiRgb {
  return { r: (hex >> 16) & 0xff, g: (hex >> 8) & 0xff, b: hex & 0xff };
}

/**
 * The 16 standard palette entries, verbatim from iOS `MobaideColors.ansiColors`.
 * Duplicated rather than derived: the web terminal's own palette lives in
 * `src/terminal/terminalTheme.ts` as xterm option strings, which are not
 * channel-extractable, and the golden asserts these exact channels.
 */
const BASE_PALETTE: readonly AnsiRgb[] = [
  rgb(0x000000), rgb(0xcd3131), rgb(0x0dbc79), rgb(0xe5e510),
  rgb(0x2472c8), rgb(0xbc3fbc), rgb(0x11a8cd), rgb(0xe5e5e5),
  rgb(0x666666), rgb(0xf14c4c), rgb(0x23d18b), rgb(0xf5f543),
  rgb(0x3b8eea), rgb(0xd670d6), rgb(0x29b8db), rgb(0xffffff),
];

function base(index: number): AnsiRgb {
  return BASE_PALETTE[Math.min(15, Math.max(0, index))] ?? BASE_PALETTE[7]!;
}

function cubeLevel(level: number): number {
  return level === 0 ? 0 : 55 + level * 40;
}

function clampChannel(value: number): number {
  return Math.min(255, Math.max(0, value));
}

function color256(n: number): AnsiRgb {
  if (n < 16) return base(n);
  if (n < 232) {
    const c = n - 16;
    return {
      r: cubeLevel(Math.floor(c / 36)),
      g: cubeLevel(Math.floor(c / 6) % 6),
      b: cubeLevel(c % 6),
    };
  }
  if (n < 256) {
    const gray = 8 + (n - 232) * 10;
    return { r: gray, g: gray, b: gray };
  }
  return base(7);
}

/**
 * Swift's `Int(_:)` semantics, not JavaScript's: a parameter that is not
 * entirely digits is `0`, where `Number.parseInt` would take the leading digits
 * of `"12abc"`. Empty parameters are `0` on both sides, which is what
 * `ESC[1;;31m` relies on.
 */
function sgrCode(text: string): number {
  return /^[+-]?\d+$/.test(text) ? Number(text) : 0;
}

/**
 * Spelled as a code point rather than a literal: a raw 0x1B byte in source is
 * invisible in every editor and diff, and `\u001b` inside a string literal is
 * one careless copy away from becoming that byte.
 */
const ESC = String.fromCodePoint(0x1b);

interface Style {
  foreground?: AnsiRgb;
  background?: AnsiRgb;
  bold: boolean;
  italic: boolean;
  underline: boolean;
}

function emptyStyle(): Style {
  return { bold: false, italic: false, underline: false };
}

function styledRun(style: Style, text: string): AnsiRun {
  const run: AnsiRun = {
    text,
    bold: style.bold,
    italic: style.italic,
    underline: style.underline,
  };
  if (style.foreground !== undefined) run.foreground = style.foreground;
  if (style.background !== undefined) run.background = style.background;
  return run;
}

/**
 * Apply a `38`/`48` extended colour. Returns how many parameters it consumed
 * beyond the selector itself — `0` when malformed, which leaves the remaining
 * parameters to be read as ordinary codes.
 */
function consumeExtended(
  codes: readonly number[],
  index: number,
  foreground: boolean,
  style: Style,
): number {
  const kind = codes[index + 1];
  if (kind === undefined) return 0;

  const assign = (value: AnsiRgb): void => {
    if (foreground) style.foreground = value;
    else style.background = value;
  };

  if (kind === 5 && index + 2 < codes.length) {
    assign(color256(codes[index + 2]!));
    return 2;
  }
  if (kind === 2 && index + 4 < codes.length) {
    assign({
      r: clampChannel(codes[index + 2]!),
      g: clampChannel(codes[index + 3]!),
      b: clampChannel(codes[index + 4]!),
    });
    return 4;
  }
  return 0;
}

function applySgr(params: string, style: Style): Style {
  const codes = params === '' ? [0] : params.split(';').map(sgrCode);
  let next = style;
  for (let j = 0; j < codes.length; j += 1) {
    const code = codes[j]!;
    if (code === 0) next = emptyStyle();
    else if (code === 1) next.bold = true;
    else if (code === 22) next.bold = false;
    else if (code === 3) next.italic = true;
    else if (code === 23) next.italic = false;
    else if (code === 4) next.underline = true;
    else if (code === 24) next.underline = false;
    else if (code >= 30 && code <= 37) next.foreground = base(code - 30);
    else if (code >= 90 && code <= 97) next.foreground = base(8 + code - 90);
    else if (code === 39) delete next.foreground;
    else if (code >= 40 && code <= 47) next.background = base(code - 40);
    else if (code >= 100 && code <= 107) next.background = base(8 + code - 100);
    else if (code === 49) delete next.background;
    else if (code === 38 || code === 48) {
      j += consumeExtended(codes, j, code === 38, next);
    }
  }
  return next;
}

function isFinalByte(char: string): boolean {
  const code = char.codePointAt(0) ?? 0;
  return code >= 0x40 && code <= 0x7e;
}

/**
 * Split a log line into styled runs.
 *
 * Non-SGR CSI sequences are consumed and dropped; an unterminated CSI drops the
 * rest of the line, which is the behaviour all three clients share.
 */
export function parseAnsiRuns(input: string): AnsiRun[] {
  // Defence in depth against a non-string reaching the render path: this runs
  // inside a React render, where a throw escapes to the router's root
  // `errorElement` and replaces the whole app rather than one line.
  if (typeof input !== 'string') return [];
  const runs: AnsiRun[] = [];
  // Code points, not UTF-16 units: a log line can carry astral characters, and
  // splitting a surrogate pair across two runs would corrupt them.
  const chars = Array.from(input);
  let style = emptyStyle();
  let buffer = '';
  let i = 0;

  const flush = (): void => {
    if (buffer === '') return;
    runs.push(styledRun(style, buffer));
    buffer = '';
  };

  while (i < chars.length) {
    const char = chars[i]!;
    if (char === ESC && chars[i + 1] === '[') {
      let j = i + 2;
      while (j < chars.length && !isFinalByte(chars[j]!)) j += 1;
      if (j >= chars.length) break;
      flush();
      if (chars[j] === 'm') {
        style = applySgr(chars.slice(i + 2, j).join(''), style);
      }
      i = j + 1;
    } else {
      buffer += char;
      i += 1;
    }
  }
  flush();
  return runs;
}

/** `AnsiRgb` as a CSS colour. */
export function ansiCss(color: AnsiRgb): string {
  return `rgb(${String(color.r)} ${String(color.g)} ${String(color.b)})`;
}

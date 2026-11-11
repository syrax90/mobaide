// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { parseAnsiRuns, type AnsiRun } from './ansi';
import { readFixture } from './__fixtures__/load';

interface FixtureRun {
  text: string;
  fg?: [number, number, number];
  bg?: [number, number, number];
  bold?: boolean;
  italic?: boolean;
  underline?: boolean;
}

interface AnsiCase {
  name: string;
  input: string;
  runs: FixtureRun[];
}

const fixture = readFixture<{ cases: AnsiCase[] }>('ansi/ansi_sgr.json');

/** The golden's shape, expanded into this parser's. */
function expected(run: FixtureRun): AnsiRun {
  const out: AnsiRun = {
    text: run.text,
    bold: run.bold === true,
    italic: run.italic === true,
    underline: run.underline === true,
  };
  if (run.fg !== undefined) out.foreground = { r: run.fg[0], g: run.fg[1], b: run.fg[2] };
  if (run.bg !== undefined) out.background = { r: run.bg[0], g: run.bg[1], b: run.bg[2] };
  return out;
}

describe('ANSI SGR golden fixtures', () => {
  // The same floor iOS asserts, so a truncated fixture file fails rather than
  // silently reducing coverage on one arm only.
  it('has cases to run', () => {
    expect(fixture.cases.length).toBeGreaterThanOrEqual(20);
  });

  it.each(fixture.cases)('parses $name', (testCase) => {
    expect(parseAnsiRuns(testCase.input)).toEqual(testCase.runs.map(expected));
  });

  it('reads ESC from the fixture as a real control byte', () => {
    // Guards the trap that has bitten every arm of this fixture: an input whose
    // ESC survived as its six literal source characters instead of decoding to
    // 0x1B would parse as text, quietly turning every colour case into a plain run.
    const coloured = fixture.cases.find((testCase) => testCase.name === 'fg_red_then_reset');
    expect(coloured?.input.codePointAt(0)).toBe(0x1b);
  });
});

describe('parseAnsiRuns beyond the golden', () => {
  it('keeps astral characters intact across a style change', () => {
    // Code-point iteration, not UTF-16 units: splitting a surrogate pair between
    // two runs would render two replacement characters.
    const esc = String.fromCodePoint(0x1b);
    const runs = parseAnsiRuns(`🙂${esc}[31m🙃`);
    expect(runs.map((run) => run.text)).toEqual(['🙂', '🙃']);
  });

  it('reads a partly-numeric parameter as 0, matching Swift Int()', () => {
    // Colon-separated SGR (`38:5:196`) is real in the wild, and `Number.parseInt`
    // would read `1:2` as 1 — keeping bold — where every other arm reads 0 and
    // resets. The wire contract is the strict parse.
    const esc = String.fromCodePoint(0x1b);
    expect(parseAnsiRuns(`${esc}[1m${esc}[1:2mA`)).toEqual([
      { text: 'A', bold: false, italic: false, underline: false },
    ]);
  });

  it('leaves a malformed extended colour without consuming the codes after it', () => {
    const esc = String.fromCodePoint(0x1b);
    const runs = parseAnsiRuns(`${esc}[38;9;1mA`);
    expect(runs[0]?.foreground).toBeUndefined();
    expect(runs[0]?.bold).toBe(true);
  });
});

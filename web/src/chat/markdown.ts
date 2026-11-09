/**
 * A small Markdown parser for agent output: text in, a plain AST out. It never
 * produces HTML — raw HTML in the source stays literal text — and a link
 * survives only with an `http`, `https` or `mailto` target. Images are never
 * loaded: `![alt](url)` becomes a link labelled with its alt text.
 *
 * Built for streaming text: an unclosed code fence runs to the end, and every
 * construct that does not close falls back to literal text.
 */

export type MdInline =
  | { t: 'text'; v: string }
  | { t: 'code'; v: string }
  | { t: 'strong' | 'em' | 'del'; c: MdInline[] }
  | { t: 'link'; href: string; c: MdInline[] }
  | { t: 'br' };

export type MdAlign = 'left' | 'center' | 'right' | null;

export interface MdListItem {
  /** `null` for a plain item, else whether the task box is checked. */
  task: boolean | null;
  c: MdBlock[];
}

export type MdBlock =
  | { t: 'p'; c: MdInline[] }
  | { t: 'h'; level: number; c: MdInline[] }
  | { t: 'code'; lang: string; text: string }
  | { t: 'quote'; c: MdBlock[] }
  | { t: 'list'; ordered: boolean; start: number; items: MdListItem[] }
  | { t: 'hr' }
  | { t: 'table'; align: MdAlign[]; head: MdInline[][]; rows: MdInline[][][] };

// `s`: lines never hold `\n`, but `.` must also cross U+2028/U+2029, or a
// failed match backtracks quadratically on a long line.
const FENCE_OPEN = /^( {0,3})(`{3,}|~{3,})(.*)$/s;
const FENCE_CLOSE = /^ {0,3}(`{3,}|~{3,})[ \t]*$/;
const RULE = /^ {0,3}(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})$/;
const QUOTE = /^ {0,3}> ?(.*)$/s;
const LIST_ITEM = /^( *)([-*+]|\d{1,9}[.)])(?:[ \t]+(.*))?$/s;
/** Quotes, lists and emphasis nested deeper than this stay literal text. */
const MAX_DEPTH = 12;
const SEPARATOR_CELL = /^:?-+:?$/;
/**
 * The empty cells one table may add. Each row is padded to the header's width,
 * so a wide header over many short rows would otherwise turn a small message
 * into millions of cells; the rows past the limit stay text.
 */
export const MAX_PADDED_CELLS = 10_000;
const TASK = /^\[([ xX])\](?:[ \t]+|$)/;

export function safeHref(raw: string): string | null {
  const candidate = raw.trim();
  if (candidate === '') return null;
  try {
    const url = new URL(candidate);
    return url.protocol === 'http:' || url.protocol === 'https:' || url.protocol === 'mailto:'
      ? url.href
      : null;
  } catch {
    return null;
  }
}

function isBlank(line: string): boolean {
  return line.trim() === '';
}

function indentOf(line: string): number {
  let n = 0;
  while (n < line.length && line[n] === ' ') n += 1;
  return n;
}

function expandLeadingTabs(line: string): string {
  const match = /^[ \t]+/.exec(line);
  if (match === null || !match[0].includes('\t')) return line;
  let width = 0;
  for (const ch of match[0]) width = ch === '\t' ? width + 4 - (width % 4) : width + 1;
  return ' '.repeat(width) + line.slice(match[0].length);
}

function stripIndent(line: string, amount: number): string {
  const strip = Math.min(amount, indentOf(line));
  return line.slice(strip);
}

function fenceOpen(line: string): { indent: number; marker: string; lang: string } | null {
  const match = FENCE_OPEN.exec(line);
  if (match === null) return null;
  const marker = match[2] ?? '';
  const info = (match[3] ?? '').trim();
  if (marker.startsWith('`') && info.includes('`')) return null;
  return { indent: (match[1] ?? '').length, marker, lang: info.split(/\s+/)[0] ?? '' };
}

function splitRow(line: string): string[] {
  let body = line.trim();
  if (body.startsWith('|')) body = body.slice(1);
  if (body.endsWith('|') && !body.endsWith('\\|')) body = body.slice(0, -1);
  const cells: string[] = [];
  let cell = '';
  let inCode = false;
  for (let i = 0; i < body.length; i += 1) {
    const ch = body[i];
    if (ch === '\\' && body[i + 1] === '|') {
      cell += '|';
      i += 1;
      continue;
    }
    if (ch === '`') inCode = !inCode;
    if (ch === '|' && !inCode) {
      cells.push(cell.trim());
      cell = '';
      continue;
    }
    cell += ch;
  }
  cells.push(cell.trim());
  return cells;
}

function tableAlign(cell: string): MdAlign {
  const left = cell.startsWith(':');
  const right = cell.endsWith(':');
  if (left && right) return 'center';
  if (right) return 'right';
  if (left) return 'left';
  return null;
}

/**
 * An ATX heading: up to three spaces, one to six `#`, then a space, a tab or
 * the end. A closing `#` run after a space is dropped. A scan, not a regex:
 * the regex backtracks quadratically on a long run of spaces.
 */
function matchHeading(line: string): { level: number; text: string } | null {
  let i = 0;
  while (i < 3 && line[i] === ' ') i += 1;
  let level = 0;
  while (line[i + level] === '#') level += 1;
  if (level < 1 || level > 6) return null;
  const next = line[i + level];
  if (next !== undefined && next !== ' ' && next !== '\t') return null;
  let text = line.slice(i + level).replace(/^[ \t]+/, '');
  let end = text.length;
  while (end > 0 && (text[end - 1] === ' ' || text[end - 1] === '\t')) end -= 1;
  text = text.slice(0, end);
  let hashes = end;
  while (hashes > 0 && text[hashes - 1] === '#') hashes -= 1;
  if (hashes < end && (hashes === 0 || text[hashes - 1] === ' ' || text[hashes - 1] === '\t')) {
    while (hashes > 0 && (text[hashes - 1] === ' ' || text[hashes - 1] === '\t')) hashes -= 1;
    text = text.slice(0, hashes);
  }
  return { level, text };
}

function startsTable(lines: readonly string[], i: number): boolean {
  const head = lines[i];
  const separator = lines[i + 1];
  if (head === undefined || separator === undefined) return false;
  if (!head.includes('|') || !separator.includes('-')) return false;
  const cells = splitRow(separator);
  // Per trimmed cell: a whole-row regex backtracks quadratically on spaces.
  if (!cells.every((cell) => SEPARATOR_CELL.test(cell.trim()))) return false;
  return splitRow(head).length === cells.length;
}

/** Whether `line` begins a block that ends a paragraph. */
function interrupts(lines: readonly string[], i: number): boolean {
  const line = lines[i] ?? '';
  return (
    fenceOpen(line) !== null ||
    matchHeading(line) !== null ||
    RULE.test(line) ||
    QUOTE.test(line) ||
    LIST_ITEM.test(line) ||
    startsTable(lines, i)
  );
}

function parseList(
  lines: readonly string[],
  start: number,
  depth: number,
): { block: MdBlock; next: number } {
  const first = LIST_ITEM.exec(lines[start] ?? '');
  const baseIndent = (first?.[1] ?? '').length;
  const ordered = /\d/.test(first?.[2] ?? '');
  const startNumber = ordered ? Number.parseInt(first?.[2] ?? '1', 10) : 1;
  const items: { lines: string[]; contentIndent: number }[] = [];
  let current: { lines: string[]; contentIndent: number } | null = null;
  let previousBlank = false;
  let i = start;

  while (i < lines.length) {
    const line = lines[i] ?? '';
    if (isBlank(line)) {
      let j = i + 1;
      while (j < lines.length && isBlank(lines[j] ?? '')) j += 1;
      const nextLine = lines[j];
      if (nextLine === undefined || current === null) break;
      const nextItem = LIST_ITEM.exec(nextLine);
      const continues =
        indentOf(nextLine) >= current.contentIndent ||
        (nextItem !== null &&
          indentOf(nextLine) >= baseIndent &&
          /\d/.test(nextItem[2] ?? '') === ordered);
      if (!continues) break;
      // The whole blank run at once: the look-ahead above already scanned it.
      for (; i < j; i += 1) current.lines.push('');
      previousBlank = true;
      continue;
    }
    const item = LIST_ITEM.exec(line);
    const indent = indentOf(line);
    if (item !== null && indent < baseIndent + 2 && !RULE.test(line)) {
      if (indent < baseIndent || /\d/.test(item[2] ?? '') !== ordered) break;
      const marker = item[2] ?? '';
      const content = item[3] ?? '';
      const gap = line.length - indent - marker.length - content.length;
      current = {
        lines: [content],
        contentIndent: indent + marker.length + Math.min(Math.max(gap, 1), 4),
      };
      items.push(current);
      previousBlank = false;
      i += 1;
      continue;
    }
    if (current === null) break;
    if (indent >= baseIndent + 2) {
      current.lines.push(stripIndent(line, current.contentIndent));
    } else if (!previousBlank && !interrupts(lines, i)) {
      current.lines.push(line.trim());
    } else {
      break;
    }
    previousBlank = false;
    i += 1;
  }

  const parsedItems: MdListItem[] = items.map(({ lines: itemLines }) => {
    const head = itemLines[0] ?? '';
    const task = TASK.exec(head);
    const body = task === null ? itemLines : [head.slice(task[0].length), ...itemLines.slice(1)];
    return {
      task: task === null ? null : task[1] !== ' ',
      c: parseBlockLines(body, depth + 1),
    };
  });
  return { block: { t: 'list', ordered, start: startNumber, items: parsedItems }, next: i };
}

function parseBlockLines(lines: readonly string[], depth = 0): MdBlock[] {
  if (depth >= MAX_DEPTH) {
    const text = lines.map((line) => line.trim()).join('\n');
    return text.trim() === '' ? [] : [{ t: 'p', c: parseInline(text) }];
  }
  const out: MdBlock[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i] ?? '';
    if (isBlank(line)) {
      i += 1;
      continue;
    }

    const fence = fenceOpen(line);
    if (fence !== null) {
      const body: string[] = [];
      i += 1;
      while (i < lines.length) {
        const candidate = lines[i] ?? '';
        const close = FENCE_CLOSE.exec(candidate);
        const closing = close?.[1];
        if (
          closing !== undefined &&
          closing[0] === fence.marker[0] &&
          closing.length >= fence.marker.length
        ) {
          i += 1;
          break;
        }
        body.push(stripIndent(candidate, fence.indent));
        i += 1;
      }
      out.push({ t: 'code', lang: fence.lang, text: body.join('\n') });
      continue;
    }

    const heading = matchHeading(line);
    if (heading !== null) {
      out.push({ t: 'h', level: heading.level, c: parseInline(heading.text) });
      i += 1;
      continue;
    }

    if (RULE.test(line)) {
      out.push({ t: 'hr' });
      i += 1;
      continue;
    }

    if (QUOTE.test(line)) {
      const inner: string[] = [];
      while (i < lines.length) {
        const match = QUOTE.exec(lines[i] ?? '');
        if (match === null) break;
        inner.push(match[1] ?? '');
        i += 1;
      }
      out.push({ t: 'quote', c: parseBlockLines(inner, depth + 1) });
      continue;
    }

    if (LIST_ITEM.test(line)) {
      const { block, next } = parseList(lines, i, depth);
      out.push(block);
      i = next;
      continue;
    }

    if (startsTable(lines, i)) {
      const head = splitRow(line);
      const align = splitRow(lines[i + 1] ?? '').map(tableAlign);
      const rows: MdInline[][][] = [];
      let padded = 0;
      i += 2;
      while (i < lines.length) {
        const row = lines[i] ?? '';
        if (isBlank(row) || !row.includes('|')) break;
        const cells = splitRow(row);
        padded += Math.max(0, head.length - cells.length);
        if (padded > MAX_PADDED_CELLS) break;
        rows.push(head.map((_, index) => parseInline(cells[index] ?? '')));
        i += 1;
      }
      out.push({ t: 'table', align, head: head.map((cell) => parseInline(cell)), rows });
      continue;
    }

    const paragraph: string[] = [line.trim()];
    i += 1;
    while (i < lines.length && !isBlank(lines[i] ?? '') && !interrupts(lines, i)) {
      paragraph.push((lines[i] ?? '').trim());
      i += 1;
    }
    out.push({ t: 'p', c: parseInline(paragraph.join('\n')) });
  }
  return out;
}

export function parseMarkdown(source: string): MdBlock[] {
  const lines = source.replace(/\r\n?/g, '\n').split('\n').map(expandLeadingTabs);
  return parseBlockLines(lines);
}

// ---------- inline ----------

const ESCAPABLE = new Set('\\`*_{}[]()#+-.!|~<>"\'');
const WORD = /[\p{L}\p{N}]/u;
const TRAILING_URL_PUNCTUATION = '.,;:!?\'"*_~';

function isWordChar(ch: string | undefined): boolean {
  return ch !== undefined && WORD.test(ch);
}

function isSpace(ch: string | undefined): boolean {
  return ch === undefined || /\s/.test(ch);
}

function runLength(s: string, i: number, ch: string): number {
  let n = 0;
  while (s[i + n] === ch) n += 1;
  return n;
}

function findCodeClose(s: string, from: number, run: number): number {
  let j = from;
  while (j < s.length) {
    if (s[j] === '`') {
      const length = runLength(s, j, '`');
      if (length === run) return j;
      j += length;
    } else {
      j += 1;
    }
  }
  return -1;
}

/** `findCodeClose` remembered per start, so repeated scans from one run cost nothing. */
type CodeCloser = (from: number, run: number) => number;

function codeCloser(s: string): CodeCloser {
  const known = new Map<number, number>();
  return (from, run) => {
    const key = from * (s.length + 1) + run;
    let close = known.get(key);
    if (close === undefined) {
      close = findCodeClose(s, from, run);
      known.set(key, close);
    }
    return close;
  };
}

interface LinkMatch {
  label: string;
  url: string;
  end: number;
}

/** A longer label stays literal text, so an unclosed `[` never scans far. */
const MAX_LINK_LABEL = 999;
/** Bounds the destination scan, so text full of unclosed `[a](` stays linear. */
const MAX_LINK_DESTINATION = 2048;

/** The first `ch` in `s` within `max` characters from `from`, or -1. */
function indexWithin(s: string, ch: string, from: number, max: number): number {
  const end = Math.min(s.length, from + max);
  for (let i = from; i < end; i += 1) {
    if (s[i] === ch) return i;
  }
  return -1;
}

function matchLink(s: string, open: number, codeClose: CodeCloser): LinkMatch | null {
  let depth = 0;
  let close = -1;
  const labelLimit = Math.min(s.length, open + MAX_LINK_LABEL + 2);
  for (let j = open; j < labelLimit; j += 1) {
    const ch = s[j];
    if (ch === '\\') {
      j += 1;
      continue;
    }
    if (ch === '`') {
      const run = runLength(s, j, '`');
      const end = codeClose(j + run, run);
      j = end === -1 ? j + run - 1 : end + run - 1;
      continue;
    }
    if (ch === '[') depth += 1;
    if (ch === ']') {
      depth -= 1;
      if (depth === 0) {
        close = j;
        break;
      }
    }
  }
  if (close === -1 || s[close + 1] !== '(') return null;
  let k = close + 2;
  // One bound for everything inside the parentheses, as on Android.
  const limit = Math.min(s.length, k + MAX_LINK_DESTINATION);
  while (k < limit && s[k] === ' ') k += 1;
  let url = '';
  if (s[k] === '<') {
    const end = indexWithin(s, '>', k, limit - k);
    if (end === -1) return null;
    url = s.slice(k + 1, end);
    k = end + 1;
  } else {
    let parens = 0;
    const begin = k;
    while (k < limit) {
      const ch = s[k];
      if (ch === undefined || /\s/.test(ch)) break;
      if (ch === '(') parens += 1;
      if (ch === ')') {
        if (parens === 0) break;
        parens -= 1;
      }
      k += 1;
    }
    if (k === limit && limit < s.length) return null;
    url = s.slice(begin, k);
  }
  while (k < limit && s[k] === ' ') k += 1;
  const quote = s[k];
  if (quote === '"' || quote === "'") {
    const end = indexWithin(s, quote, k + 1, limit - k - 1);
    if (end === -1) return null;
    k = end + 1;
    while (k < limit && s[k] === ' ') k += 1;
  }
  if (k >= limit || s[k] !== ')') return null;
  return { label: s.slice(open + 1, close), url, end: k + 1 };
}

/** The closing delimiter for an emphasis run starting at `from`, or -1. */
function findEmphasisClose(
  s: string,
  from: number,
  ch: string,
  size: number,
  codeClose: CodeCloser,
): number {
  let j = from;
  while (j < s.length) {
    const c = s[j];
    if (c === '\\') {
      j += 2;
      continue;
    }
    if (c === '`') {
      const run = runLength(s, j, '`');
      const end = codeClose(j + run, run);
      j = end === -1 ? j + run : end + run;
      continue;
    }
    if (c === ch) {
      const run = runLength(s, j, ch);
      const candidate = run > size && size === 2 ? j + run - 2 : j;
      const fits = size === 2 ? run >= 2 : run === 1 || run === 3;
      const after = s[candidate + size];
      if (
        fits &&
        candidate > from &&
        !isSpace(s[candidate - 1]) &&
        (ch !== '_' || !isWordChar(after))
      ) {
        return size === 1 && run === 3 ? j + 2 : candidate;
      }
      j += run;
      continue;
    }
    j += 1;
  }
  return -1;
}

/** Drops trailing punctuation and unbalanced `)` from a bare URL, in one pass. */
function trimUrl(url: string): string {
  let opens = 0;
  let closes = 0;
  for (let i = 0; i < url.length; i += 1) {
    if (url[i] === '(') opens += 1;
    else if (url[i] === ')') closes += 1;
  }
  let end = url.length;
  while (end > 0) {
    const ch = url[end - 1] ?? '';
    if (TRAILING_URL_PUNCTUATION.includes(ch)) {
      end -= 1;
    } else if (ch === ')' && closes > opens) {
      closes -= 1;
      end -= 1;
    } else {
      break;
    }
  }
  return url.slice(0, end);
}

/** The only characters that make a closer scan step differently from `parseInline`. */
const SCAN_STEP = /[`\\]/;

export function parseInline(source: string, allowLinks = true, depth = 0): MdInline[] {
  if (depth >= MAX_DEPTH) return source === '' ? [] : [{ t: 'text', v: source }];
  const out: MdInline[] = [];
  let buffer = '';
  const flush = (): void => {
    if (buffer !== '') {
      out.push({ t: 'text', v: buffer });
      buffer = '';
    }
  };
  const s = source;
  const codeClose = codeCloser(s);

  // A closer search that found nothing from `from` finds nothing from any
  // later start: it would scan a suffix of the same text in the same steps.
  // Without this, every unmatched `*` rescans to the end of the paragraph.
  const unclosed = new Map<string, number>();
  const findClose = (key: string, from: number, search: () => number): number => {
    const known = unclosed.get(key);
    if (known !== undefined && from >= known) return -1;
    const close = search();
    if (close === -1) unclosed.set(key, from);
    return close;
  };
  // Jumping over a link target, a URL or a strikethrough is not a step the
  // emphasis scan takes; a code span or escape inside one can shift it.
  const jumpOver = (from: number, to: number): number => {
    if (SCAN_STEP.test(s.slice(from, to))) unclosed.clear();
    return to;
  };

  let i = 0;
  while (i < s.length) {
    const ch = s[i] ?? '';

    if (ch === '\\') {
      const next = s[i + 1];
      if (next === '\n') {
        flush();
        out.push({ t: 'br' });
        i += 2;
        continue;
      }
      if (next !== undefined && ESCAPABLE.has(next)) {
        buffer += next;
        i += 2;
        continue;
      }
      buffer += ch;
      i += 1;
      continue;
    }

    if (ch === '\n') {
      flush();
      out.push({ t: 'br' });
      i += 1;
      continue;
    }

    if (ch === '`') {
      const run = runLength(s, i, '`');
      const close = codeClose(i + run, run);
      if (close !== -1) {
        let code = s.slice(i + run, close).replace(/\n/g, ' ');
        if (code.length > 2 && code.startsWith(' ') && code.endsWith(' ') && code.trim() !== '') {
          code = code.slice(1, -1);
        }
        flush();
        out.push({ t: 'code', v: code });
        i = close + run;
        continue;
      }
      buffer += '`'.repeat(run);
      i += run;
      continue;
    }

    if (allowLinks && (ch === '[' || (ch === '!' && s[i + 1] === '['))) {
      const open = ch === '!' ? i + 1 : i;
      const link = matchLink(s, open, codeClose);
      if (link !== null) {
        flush();
        const href = safeHref(link.url);
        const label = parseInline(link.label, false, depth + 1);
        const children = label.length > 0 ? label : [{ t: 'text' as const, v: link.url }];
        if (href !== null) out.push({ t: 'link', href, c: children });
        else out.push(...children);
        // The label is scanned in the same steps as emphasis; the target is not.
        i = jumpOver(open + 1 + link.label.length, link.end);
        continue;
      }
    }

    if (allowLinks && ch === '<') {
      const auto = /^<((?:https?|mailto):[^\s<>]+)>/i.exec(s.slice(i));
      const href = auto?.[1] === undefined ? null : safeHref(auto[1]);
      if (auto !== null && href !== null) {
        flush();
        out.push({ t: 'link', href, c: [{ t: 'text', v: auto[1] ?? '' }] });
        i = jumpOver(i, i + auto[0].length);
        continue;
      }
    }

    if (allowLinks && (ch === 'h' || ch === 'H') && !isWordChar(s[i - 1])) {
      const bare = /^https?:\/\/[^\s<>`]+/i.exec(s.slice(i));
      if (bare !== null) {
        const url = trimUrl(bare[0]);
        const href = safeHref(url);
        if (href !== null && url.length > 'https://'.length - 1) {
          flush();
          out.push({ t: 'link', href, c: [{ t: 'text', v: url }] });
          i = jumpOver(i, i + url.length);
          continue;
        }
        // A rejected run stays text as a whole (as on Android): retrying it
        // from every later `http` rescans the rest of the run each time.
        buffer += bare[0];
        i += bare[0].length;
        continue;
      }
    }

    if (ch === '~' && s[i + 1] === '~' && !isSpace(s[i + 2])) {
      const from = i + 2;
      const close = findClose('~~', from, () => findEmphasisClose(s, from, '~', 2, codeClose));
      if (close !== -1 && !isSpace(s[close - 1])) {
        flush();
        out.push({ t: 'del', c: parseInline(s.slice(i + 2, close), allowLinks, depth + 1) });
        i = jumpOver(i, close + 2);
        continue;
      }
    }

    if (ch === '*' || ch === '_') {
      const run = runLength(s, i, ch);
      const leftOk = ch === '*' || !isWordChar(s[i - 1]);
      if (run >= 2 && leftOk && !isSpace(s[i + 2])) {
        const from = i + 2;
        const close = findClose(ch + ch, from, () => findEmphasisClose(s, from, ch, 2, codeClose));
        if (close !== -1) {
          flush();
          out.push({ t: 'strong', c: parseInline(s.slice(i + 2, close), allowLinks, depth + 1) });
          i = close + 2;
          continue;
        }
      }
      if (leftOk && !isSpace(s[i + 1])) {
        const from = i + 1;
        const close = findClose(ch, from, () => findEmphasisClose(s, from, ch, 1, codeClose));
        if (close !== -1) {
          flush();
          out.push({ t: 'em', c: parseInline(s.slice(i + 1, close), allowLinks, depth + 1) });
          i = close + 1;
          continue;
        }
      }
      buffer += ch.repeat(run);
      i += run;
      continue;
    }

    buffer += ch;
    i += 1;
  }
  flush();
  return out;
}

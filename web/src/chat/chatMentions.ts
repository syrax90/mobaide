/**
 * `@` file mentions in the chat composer: which token the caret is in, what
 * a picked suggestion turns it into, and which picked files the text still
 * names when it is sent. Android `ChatMentions.kt` and iOS
 * `ChatMentions.swift` follow the same rules.
 */

/** The agent takes at most this many per prompt. */
export const MAX_MENTIONS = 10;
/** How far back from the caret a picked path's `@` is looked for (the agent's path cap). */
const PICKED_SCAN_MAX = 4096;

/** The `@` token around the caret: `[start, end)`; `query` is what is typed after the `@`. */
export interface MentionToken {
  start: number;
  end: number;
  query: string;
}

const isSpace = (ch: string | undefined): boolean => ch !== undefined && /\s/.test(ch);
const TRAILING_PUNCTUATION = /[.,;:!?)\]}'"]/;

/**
 * The token the caret is in when it starts with `@` at the start of the text
 * or after whitespace, with no whitespace up to the caret; null otherwise. The
 * `@` must be before the caret: a caret just in front of it is not in the token.
 * A `picked` path counts as one token even with spaces in it (`@my docs/`),
 * together with what is typed right after it.
 */
export function mentionAt(
  text: string,
  caret: number,
  picked: readonly string[] = [],
): MentionToken | null {
  const at = Math.min(Math.max(caret, 0), text.length);
  return pickedMentionAt(text, at, picked) ?? plainMentionAt(text, at);
}

function plainMentionAt(text: string, at: number): MentionToken | null {
  let start = at;
  while (start > 0 && !isSpace(text[start - 1])) start -= 1;
  if (start === at || text[start] !== '@') return null;
  let end = at;
  while (end < text.length && !isSpace(text[end])) end += 1;
  return { start, end, query: text.slice(start + 1, at) };
}

/** The token of a picked path the caret is in, found from the nearest `@` before the caret. */
function pickedMentionAt(text: string, at: number, picked: readonly string[]): MentionToken | null {
  if (picked.length === 0) return null;
  const floor = Math.max(0, at - PICKED_SCAN_MAX);
  for (let p = at - 1; p >= floor && text[p] !== '\n'; p -= 1) {
    if (text[p] !== '@' || (p > 0 && !isSpace(text[p - 1]))) continue;
    let longest = -1;
    for (const value of picked) {
      if (value.length > longest && text.startsWith(value, p + 1)) longest = value.length;
    }
    if (longest < 0) continue;
    let end = p + 1 + longest;
    while (end < text.length && !isSpace(text[end])) end += 1;
    if (at <= end) return { start: p, end, query: text.slice(p + 1, at) };
  }
  return null;
}

/** What the composer holds for a picked entry: folders end in `/`. */
export function mentionValue(path: string, dir: boolean): string {
  return dir ? `${path}/` : path;
}

/**
 * The text with `token` replaced by `@value`. A file is followed by a space
 * (unless one is already there); a folder is not, so typing goes on inside it.
 */
export function insertMention(
  text: string,
  token: MentionToken,
  value: string,
): { text: string; caret: number } {
  const before = text.slice(0, token.start);
  const after = text.slice(token.end);
  const folder = value.endsWith('/');
  const inserted = `@${value}`;
  if (folder) return { text: before + inserted + after, caret: before.length + inserted.length };
  const space = isSpace(after[0]) ? '' : ' ';
  return {
    text: before + inserted + space + after,
    caret: before.length + inserted.length + 1,
  };
}

/** Whether `@value` stands as a whole token in `text` (trailing punctuation allowed). */
function namesMention(text: string, value: string): boolean {
  const token = `@${value}`;
  for (let at = text.indexOf(token); at >= 0; at = text.indexOf(token, at + 1)) {
    if (at > 0 && !isSpace(text[at - 1])) continue;
    const end = at + token.length;
    if (end === text.length || isSpace(text[end])) return true;
    if (TRAILING_PUNCTUATION.test(text[end] ?? '') && (end + 1 === text.length || isSpace(text[end + 1]))) {
      return true;
    }
  }
  return false;
}

/** The picked mentions whose token is still in `text`, in pick order, at most {@link MAX_MENTIONS}. */
export function mentionsInText(text: string, picked: readonly string[]): string[] {
  return [...new Set(picked)].filter((value) => namesMention(text, value)).slice(0, MAX_MENTIONS);
}

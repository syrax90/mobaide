import { describe, expect, it } from 'vitest';
import { MAX_MENTIONS, insertMention, mentionAt, mentionsInText } from './chatMentions';

describe('mentionAt', () => {
  it.each([
    { text: '@sr', caret: 3, token: { start: 0, end: 3, query: 'sr' } },
    { text: 'see @src/ap now', caret: 11, token: { start: 4, end: 11, query: 'src/ap' } },
    // Back inside a picked mention: the query is what lies before the caret.
    { text: '@src/app.py', caret: 4, token: { start: 0, end: 11, query: 'src' } },
  ])('finds the token in "$text" at $caret', ({ text, caret, token }) => {
    expect(mentionAt(text, caret)).toEqual(token);
  });

  it.each([
    { text: 'mail me@host.com', caret: 16 },
    { text: 'a @b c', caret: 6 },
    { text: 'plain', caret: 3 },
    // A caret just in front of the '@' (at 0 or after whitespace) is not in the token.
    { text: '@sr', caret: 0 },
    { text: 'hi @', caret: 3 },
  ])('finds none in "$text" at $caret', ({ text, caret }) => {
    expect(mentionAt(text, caret)).toBeNull();
  });
});

describe('insertMention', () => {
  it('ends a file with one space and puts the caret after it', () => {
    const atEnd = '@sr';
    const midText = 'fix @sr now';

    expect(insertMention(atEnd, { start: 0, end: 3, query: 'sr' }, 'src/app.py')).toEqual({
      text: '@src/app.py ',
      caret: 12,
    });
    expect(insertMention(midText, { start: 4, end: 7, query: 'sr' }, 'src/app.py')).toEqual({
      text: 'fix @src/app.py now',
      caret: 16,
    });
  });

  it('leaves the caret inside a folder, so its files are suggested next', () => {
    const picked = insertMention('@sr', { start: 0, end: 3, query: 'sr' }, 'src/');

    expect(picked).toEqual({ text: '@src/', caret: 5 });
    expect(mentionAt(picked.text, picked.caret)?.query).toBe('src/');
  });
});

describe('mentionsInText', () => {
  it('sends the picked mentions the text still names, in pick order, once each', () => {
    const picked = ['src/app.py', 'gone.ts', 'docs/', 'src/app.py', 'lib/x.ts', 'lib/y.ts'];
    const text = 'Explain @src/app.py, then @docs/ (see lib/x.ts) x@lib/y.ts @src/app.py.bak';

    expect(mentionsInText(text, picked)).toEqual(['src/app.py', 'docs/']);
  });

  it('sends at most MAX_MENTIONS', () => {
    const picked = Array.from({ length: MAX_MENTIONS + 2 }, (_, n) => `f${String(n)}.ts`);

    expect(mentionsInText(picked.map((path) => `@${path}`).join(' '), picked)).toEqual(
      picked.slice(0, MAX_MENTIONS),
    );
  });
});

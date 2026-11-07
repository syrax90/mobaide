import { describe, expect, it } from 'vitest';
import { parseInline, parseMarkdown, safeHref, type MdInline } from './markdown';

function text(v: string): MdInline {
  return { t: 'text', v };
}

function para(...c: MdInline[]) {
  return { t: 'p', c };
}

describe('safeHref', () => {
  it.each([
    ['https://example.com/a', 'https://example.com/a'],
    [' http://a.b ', 'http://a.b/'],
    ['mailto:dev@example.com', 'mailto:dev@example.com'],
  ])('keeps %s', (raw, href) => {
    expect(safeHref(raw)).toBe(href);
  });

  it.each(['javascript:alert(1)', 'data:text/html,hi', 'file:///etc/passwd', '/relative', ''])(
    'drops %s',
    (raw) => {
      expect(safeHref(raw)).toBeNull();
    },
  );
});

describe('parseMarkdown blocks', () => {
  it('reads headings and paragraphs', () => {
    expect(parseMarkdown('## Result ##\n\nfirst\nsecond')).toEqual([
      { t: 'h', level: 2, c: [text('Result')] },
      para(text('first'), { t: 'br' }, text('second')),
    ]);
  });

  it('reads a fenced code block with its language', () => {
    expect(parseMarkdown('```python\nx = 1\n\ny = *2*\n```\nafter')).toEqual([
      { t: 'code', lang: 'python', text: 'x = 1\n\ny = *2*' },
      para(text('after')),
    ]);
  });

  it('runs an unclosed fence to the end while streaming', () => {
    expect(parseMarkdown('```\nline 1\nline 2')).toEqual([
      { t: 'code', lang: '', text: 'line 1\nline 2' },
    ]);
  });

  it('keeps raw HTML as literal text', () => {
    expect(parseMarkdown('<script>alert(1)</script>')).toEqual([
      para(text('<script>alert(1)</script>')),
    ]);
  });

  it('reads nested and numbered lists', () => {
    expect(parseMarkdown('3. one\n4. two\n   - nested')).toEqual([
      {
        t: 'list',
        ordered: true,
        start: 3,
        items: [
          { task: null, c: [para(text('one'))] },
          {
            task: null,
            c: [
              para(text('two')),
              {
                t: 'list',
                ordered: false,
                start: 1,
                items: [{ task: null, c: [para(text('nested'))] }],
              },
            ],
          },
        ],
      },
    ]);
  });

  it('reads task list boxes', () => {
    const [list] = parseMarkdown('- [x] done\n- [ ] todo\n- plain');
    expect(list).toEqual({
      t: 'list',
      ordered: false,
      start: 1,
      items: [
        { task: true, c: [para(text('done'))] },
        { task: false, c: [para(text('todo'))] },
        { task: null, c: [para(text('plain'))] },
      ],
    });
  });

  it('reads a table with alignment', () => {
    expect(parseMarkdown('| File | Lines |\n|:-----|------:|\n| `a|b.py` | 120 |\n\nnext')).toEqual([
      {
        t: 'table',
        align: ['left', 'right'],
        head: [[text('File')], [text('Lines')]],
        rows: [[[{ t: 'code', v: 'a|b.py' }], [text('120')]]],
      },
      para(text('next')),
    ]);
  });

  it('reads quotes and rules, and a paragraph ends at a block', () => {
    expect(parseMarkdown('> Note: **run** it\nplain\n---\n# Title')).toEqual([
      { t: 'quote', c: [para(text('Note: '), { t: 'strong', c: [text('run')] }, text(' it'))] },
      para(text('plain')),
      { t: 'hr' },
      { t: 'h', level: 1, c: [text('Title')] },
    ]);
  });

  it('treats CRLF as a line break', () => {
    expect(parseMarkdown('a\r\nb')).toEqual([para(text('a'), { t: 'br' }, text('b'))]);
  });
});

describe('parseInline', () => {
  it('reads emphasis, strike and code', () => {
    expect(parseInline('**b** *i* _u_ ~~d~~ ``a ` b``')).toEqual([
      { t: 'strong', c: [text('b')] },
      text(' '),
      { t: 'em', c: [text('i')] },
      text(' '),
      { t: 'em', c: [text('u')] },
      text(' '),
      { t: 'del', c: [text('d')] },
      text(' '),
      { t: 'code', v: 'a ` b' },
    ]);
  });

  it('leaves underscores inside words and unclosed markers alone', () => {
    expect(parseInline('snake_case_name and *open')).toEqual([text('snake_case_name and *open')]);
  });

  it('honours backslash escapes', () => {
    expect(parseInline('\\*not em\\* \\[x\\]')).toEqual([text('*not em* [x]')]);
  });

  it('keeps safe links and drops unsafe targets to their label', () => {
    expect(parseInline('[docs](https://example.com/a "Title") [x](javascript:alert(1))')).toEqual([
      { t: 'link', href: 'https://example.com/a', c: [text('docs')] },
      text(' '),
      text('x'),
    ]);
  });

  it('turns an image into a link labelled with its alt text', () => {
    expect(parseInline('![logo](https://example.com/l.png)')).toEqual([
      { t: 'link', href: 'https://example.com/l.png', c: [text('logo')] },
    ]);
  });

  it('links bare and angle-bracket URLs without trailing punctuation', () => {
    expect(parseInline('See https://example.com/x. (https://e.com/a) <mailto:a@b.co>')).toEqual([
      text('See '),
      { t: 'link', href: 'https://example.com/x', c: [text('https://example.com/x')] },
      text('. ('),
      { t: 'link', href: 'https://e.com/a', c: [text('https://e.com/a')] },
      text(') '),
      { t: 'link', href: 'mailto:a@b.co', c: [text('mailto:a@b.co')] },
    ]);
  });

  it('does not nest links inside a link label', () => {
    expect(parseInline('[see https://inner.com](https://outer.com)')).toEqual([
      { t: 'link', href: 'https://outer.com/', c: [text('see https://inner.com')] },
    ]);
  });
});

describe('parseInline unmatched delimiters', () => {
  it('keeps emphasis after a link whose target holds a backtick', () => {
    expect(parseInline('*a [x](http://e/`) *b* c` d')).toEqual([
      text('*a '),
      { t: 'link', href: 'http://e/%60', c: [text('x')] },
      text(' '),
      { t: 'em', c: [text('b')] },
      text(' c` d'),
    ]);
  });

  it('keeps emphasis after an angle-bracket URL that holds a backtick', () => {
    expect(parseInline('*a <http://e/`> *b* c` d')).toContainEqual({ t: 'em', c: [text('b')] });
  });

  it.each<[string, MdInline[]]>([
    [
      '~~a `~~` b~~',
      [{ t: 'del', c: [text('a '), { t: 'code', v: '~~' }, text(' b')] }],
    ],
    ['*a ~~x`~~ *b* c` d', [text('*a ~~x'), { t: 'code', v: '~~ *b* c' }, text(' d')]],
    [
      '*a http://e/` *b* c` d',
      [
        text('*a '),
        { t: 'link', href: 'http://e/', c: [text('http://e/')] },
        { t: 'code', v: ' *b* c' },
        text(' d'),
      ],
    ],
    ['~~a \\~~ b~~', [{ t: 'del', c: [text('a ~~ b')] }]],
    [
      '[see https://inner.com](https://outer.com)',
      [{ t: 'link', href: 'https://outer.com/', c: [text('see https://inner.com')] }],
    ],
  ])('reads code spans and escapes before a strikethrough or bare URL in %j', (source, expected) => {
    expect(parseInline(source)).toEqual(expected);
  });

  it.each(['*a ', '_a ', '**a ', '__a '])(
    'reads 50 000 unmatched %j openers as plain text without rescanning',
    (opener) => {
      const source = opener.repeat(50_000);
      expect(parseInline(source)).toEqual([text(source)]);
    },
    1_000,
  );

  it('caps a link label at 999 characters', () => {
    const label = 'a'.repeat(999);
    expect(parseInline(`[${label}](https://e.com)`)).toEqual([
      { t: 'link', href: 'https://e.com/', c: [text(label)] },
    ]);

    const tooLong = 'a'.repeat(1000);
    expect(parseInline(`[${tooLong}](https://e.com)`)).toEqual([
      text(`[${tooLong}](`),
      { t: 'link', href: 'https://e.com/', c: [text('https://e.com')] },
      text(')'),
    ]);
  });
});

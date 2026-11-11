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

  it('reads a separator row of 20 000 cells', () => {
    const columns = 20_000;
    expect(parseMarkdown(`${'|a'.repeat(columns)}|\n${'|-'.repeat(columns)}|`)).toEqual([
      {
        t: 'table',
        align: Array(columns).fill(null),
        head: Array.from({ length: columns }, () => [text('a')]),
        rows: [],
      },
    ]);
    expect(parseMarkdown(`a|b\n${'|-'.repeat(columns)}|`)).toEqual([
      para(text('a|b'), { t: 'br' }, text(`${'|-'.repeat(columns)}|`)),
    ]);
  });

  it('stops a table whose short rows would pad past the cell limit', () => {
    // 1 000 columns over rows of one cell: each row adds 999 empty cells.
    const columns = 1_000;
    const source = `${'|a'.repeat(columns)}|\n${'|-'.repeat(columns)}|\n${'x|\n'.repeat(50)}`;
    const [table, rest, ...others] = parseMarkdown(source);
    expect(table?.t).toBe('table');
    expect(table?.t === 'table' ? table.rows.length : -1).toBe(10);
    expect(rest?.t).toBe('p');
    expect(others).toEqual([]);
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

  it.each([
    ['U+2028', '\u2028'],
  ])('reads a list item, a fence and a quote whose line holds a %s', (_name, sep) => {
    expect(parseMarkdown(`- a${sep}b`)).toEqual([
      { t: 'list', ordered: false, start: 1, items: [{ task: null, c: [para(text(`a${sep}b`))] }] },
    ]);
    expect(parseMarkdown(`\`\`\`py x${sep}y\ncode\n\`\`\``)).toEqual([
      { t: 'code', lang: 'py', text: 'code' },
    ]);
    expect(parseMarkdown(`> a${sep}b`)).toEqual([{ t: 'quote', c: [para(text(`a${sep}b`))] }]);
  });

  it(
    'reads a 128 000-character list or fence line holding a U+2028 without backtracking',
    () => {
      expect(parseMarkdown(`- ${' '.repeat(128_000)}a\u2028b`)).toEqual([
        { t: 'list', ordered: false, start: 1, items: [{ task: null, c: [para(text('a\u2028b'))] }] },
      ]);
      expect(parseMarkdown(`${'`'.repeat(128_000)}py x\u2028y`)).toEqual([
        { t: 'code', lang: 'py', text: '' },
      ]);
    },
    1_000,
  );

  it(
    'continues a list item across a run of 100 000 blank lines in one pass',
    () => {
      expect(parseMarkdown(`- a\n${'\n'.repeat(100_000)}  b\n- c`)).toEqual([
        {
          t: 'list',
          ordered: false,
          start: 1,
          items: [
            { task: null, c: [para(text('a')), para(text('b'))] },
            { task: null, c: [para(text('c'))] },
          ],
        },
      ]);
    },
    1_000,
  );

  it('ends a list at a blank run followed by an unindented line', () => {
    expect(parseMarkdown('- a\n\n\n\nb')).toEqual([
      { t: 'list', ordered: false, start: 1, items: [{ task: null, c: [para(text('a'))] }] },
      para(text('b')),
    ]);
  });
});

describe('parseMarkdown headings', () => {
  it.each<[string, number, MdInline[]]>([
    ['# foo #', 1, [text('foo')]],
    ['### ###', 3, []],
    ['# foo \\#', 1, [text('foo #')]],
    ['# foo # bar', 1, [text('foo # bar')]],
  ])('reads %j as a level %i heading', (source, level, c) => {
    expect(parseMarkdown(source)).toEqual([{ t: 'h', level, c }]);
  });

  it.each(['#hashtag', '####### seven'])('keeps %j a paragraph', (source) => {
    expect(parseMarkdown(source)).toEqual([para(text(source))]);
  });

  it(
    'reads a heading with 120 000 inner spaces without backtracking',
    () => {
      const spaces = ' '.repeat(120_000);
      expect(parseMarkdown(`# a${spaces}b`)).toEqual([
        { t: 'h', level: 1, c: [text(`a${spaces}b`)] },
      ]);
      expect(parseMarkdown(`# a${spaces}#${spaces}`)).toEqual([
        { t: 'h', level: 1, c: [text('a')] },
      ]);
    },
    1_000,
  );
});

describe('parseMarkdown nesting depth', () => {
  function nested(levels: number, wrap: (c: unknown[]) => unknown, inner: unknown[]): unknown[] {
    let blocks = inner;
    for (let level = 0; level < levels; level += 1) blocks = [wrap(blocks)];
    return blocks;
  }

  const quote = (c: unknown[]) => ({ t: 'quote', c });
  const bullet = (c: unknown[]) => ({
    t: 'list',
    ordered: false,
    start: 1,
    items: [{ task: null, c }],
  });

  it('nests 12 quote levels and keeps deeper markers as one literal paragraph', () => {
    expect(parseMarkdown(`${'>'.repeat(12)} deep`)).toEqual(
      nested(12, quote, [para(text('deep'))]),
    );
    expect(parseMarkdown(`${'>'.repeat(10_000)} deep`)).toEqual(
      nested(12, quote, [para(text(`${'>'.repeat(9_988)} deep`))]),
    );
  });

  it('nests 12 list levels and keeps deeper markers as one literal paragraph', () => {
    expect(parseMarkdown(`${'- '.repeat(12)}deep`)).toEqual(
      nested(12, bullet, [para(text('deep'))]),
    );
    expect(parseMarkdown(`${'- '.repeat(10_000)}deep`)).toEqual(
      nested(12, bullet, [para(text(`${'- '.repeat(9_988)}deep`))]),
    );
  });

  it('drops whitespace-only lines below the depth limit', () => {
    expect(parseMarkdown(`${'>'.repeat(12)}\n${'>'.repeat(12)}`)).toEqual(nested(12, quote, []));
  });

  it.each([
    ['*', 'strong'],
    ['_', 'strong'],
    ['~', 'del'],
  ])('nests a run of 20 000 %j markers 12 levels deep and keeps the rest literal', (marker, t) => {
    expect(parseMarkdown(`a ${marker.repeat(20_000)}`)).toEqual([
      {
        t: 'p',
        c: [text('a '), ...nested(12, (c) => ({ t, c }), [text(marker.repeat(19_952))])],
      },
    ]);
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

  it.each(['*a ', '**a '])(
    'reads 50 000 unmatched %j openers as plain text without rescanning',
    (opener) => {
      const source = opener.repeat(50_000);
      expect(parseInline(source)).toEqual([text(source)]);
    },
    1_000,
  );

  it(
    'scans 1 000 unclosed brackets over 400 unmatched code runs without rescanning',
    () => {
      const runs = Array.from({ length: 400 }, (_, n) => '`'.repeat(n + 1)).join('a');
      const source = `${'['.repeat(1_000)}${runs}`;
      expect(parseInline(source)).toEqual([text(source)]);
    },
    1_000,
  );

  it('reads a code span that a failed link scan already skipped', () => {
    expect(parseInline('[`a]` b')).toEqual([text('['), { t: 'code', v: 'a]' }, text(' b')]);
  });

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

  it('links a 2 000-character destination', () => {
    const url = 'https://e.com/'.padEnd(2_000, 'a');
    expect(parseInline(`[x](${url})`)).toEqual([{ t: 'link', href: url, c: [text('x')] }]);
    expect(parseInline(`[x](<${url}>)`)).toEqual([{ t: 'link', href: url, c: [text('x')] }]);
  });

  const longUrl = 'https://e.com/'.padEnd(3_000, 'a');
  const longTitle = 't'.repeat(3_000);

  it.each<[string, string, MdInline[]]>([
    [
      'destination',
      `[x](${longUrl})`,
      [text('[x]('), { t: 'link', href: longUrl, c: [text(longUrl)] }, text(')')],
    ],
    [
      'angle-bracket destination',
      `[x](<${longUrl}>)`,
      [text('[x]('), { t: 'link', href: longUrl, c: [text(longUrl)] }, text(')')],
    ],
    [
      'title',
      `[x](https://e.com "${longTitle}")`,
      [
        text('[x]('),
        { t: 'link', href: 'https://e.com/', c: [text('https://e.com')] },
        text(` "${longTitle}")`),
      ],
    ],
  ])('keeps a link whose %s runs past 2 048 characters literal', (_part, source, expected) => {
    expect(parseInline(source)).toEqual(expected);
  });

  it('counts the destination and the title against one 2 048-character bound', () => {
    const url = 'https://e.com/'.padEnd(1_500, 'a');
    const title = 't'.repeat(1_000);
    const autolink: MdInline = { t: 'link', href: url, c: [text(url)] };
    expect(parseInline(`[x](<${url}> "${title}")`)).toEqual([
      text('[x]('),
      autolink,
      text(` "${title}")`),
    ]);
    expect(parseInline(`[x](${url} "${title}")`)).toEqual([
      text('[x]('),
      autolink,
      text(` "${title}")`),
    ]);

    const shortUrl = 'https://e.com/'.padEnd(1_000, 'a');
    expect(parseInline(`[x](<${shortUrl}> "${'t'.repeat(500)}")`)).toEqual([
      { t: 'link', href: shortUrl, c: [text('x')] },
    ]);
  });

  it('trims unbalanced closing parens and punctuation from a bare URL together', () => {
    expect(parseInline('(see https://e.com/w_(x)).')).toEqual([
      text('(see '),
      { t: 'link', href: 'https://e.com/w_(x)', c: [text('https://e.com/w_(x)')] },
      text(').'),
    ]);
  });

  it(
    'trims 40 000 unbalanced closing parens from a bare URL in one pass',
    () => {
      const closes = ')'.repeat(40_000);
      expect(parseInline(`see https://e.com/a${closes}`)).toEqual([
        text('see '),
        { t: 'link', href: 'https://e.com/a', c: [text('https://e.com/a')] },
        text(closes),
      ]);
    },
    1_000,
  );

  it('keeps a bare URL that fails safeHref as one text run up to the next space', () => {
    expect(parseInline('x http://^/https://e.com')).toEqual([text('x http://^/https://e.com')]);
    expect(parseInline('see http://^/ ok https://e.com')).toEqual([
      text('see http://^/ ok '),
      { t: 'link', href: 'https://e.com/', c: [text('https://e.com')] },
    ]);
  });

  it(
    'keeps a run of 30 000 rejected bare URLs as text in one pass',
    () => {
      const source = `x ${'http://^/'.repeat(30_000)}`;
      expect(parseInline(source)).toEqual([text(source)]);
    },
    1_000,
  );
});

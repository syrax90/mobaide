import { describe, expect, it } from 'vitest';
import type { SessionGitConflict } from '../../../api/sessionGitRepository';
import {
  canStartOver,
  canTakeSide,
  conflictKindNotice,
  exceedsByteLimit,
  isEditableConflict,
  nextConflictOffset,
  scanConflictText,
  takeChoices,
} from './sessionGitConflictDisplay';

const conflict = (over: Partial<SessionGitConflict> = {}): SessionGitConflict => ({
  path: 'src/login.py',
  code: 'UU',
  state: 'rebasing',
  kind: 'text',
  content: 'a\n<<<<<<< HEAD\nmain\n=======\ntopic\n>>>>>>> fedcba9 (Fix the login redirect)\n',
  token: 't1',
  markers: 1,
  has_base: false,
  ours: { label: 'origin/main', present: true },
  theirs: { label: 'fedcba9 Fix the login redirect', present: true },
  byte_limit: 262144,
  ...over,
});

const TWO_BLOCKS = 'top\n<<<<<<< a\nx\n=======\ny\n>>>>>>> b\nmid\n<<<<<<< a\nz\n=======\n>>>>>>> b\n';

describe('scanConflictText', () => {
  it('counts one conflict per start marker and sees no base in a two-way block', () => {
    expect(scanConflictText(TWO_BLOCKS)).toEqual({ conflicts: 2, hasMarkers: true, hasBase: false });
  });

  it('sees the original-lines section of a diff3 block', () => {
    expect(scanConflictText('<<<<<<< ours\nm\n||||||| base\no\n=======\nt\n>>>>>>> theirs\n')).toEqual({
      conflicts: 1,
      hasMarkers: true,
      hasBase: true,
    });
  });

  it('finds nothing in resolved text, a bare separator included', () => {
    // `=======` alone is valid Markdown and AsciiDoc: the agent accepts it too.
    expect(scanConflictText('title\n=======\nbody\n')).toEqual({
      conflicts: 0,
      hasMarkers: false,
      hasBase: false,
    });
    expect(scanConflictText('')).toEqual({ conflicts: 0, hasMarkers: false, hasBase: false });
  });

  it('still reports a leftover end marker, which the agent refuses, as no open conflict', () => {
    expect(scanConflictText('kept\n>>>>>>> fedcba9 (Fix)\n')).toEqual({
      conflicts: 0,
      hasMarkers: true,
      hasBase: false,
    });
  });

  it.each([
    ['a bare marker', '<<<<<<<', true],
    ['a marker before CRLF', '<<<<<<<\r\nx', true],
    ['more than seven characters', '<<<<<<<<< label', true],
    ['six characters', '<<<<<< label', false],
    ['a label glued to the marker', '<<<<<<<label', false],
    ['an indented marker', ' <<<<<<< label', false],
    ['a marker mid-line', 'x <<<<<<< label', false],
    // JavaScript's `\s` takes these for whitespace; Python's bytes `\s` does not.
    ['a no-break space after the marker', '<<<<<<< label', false],
  ])('matches the agent on %s', (_label, text, marker) => {
    expect(scanConflictText(text).hasMarkers).toBe(marker);
    expect(scanConflictText(text).conflicts).toBe(marker ? 1 : 0);
  });
});

describe('nextConflictOffset', () => {
  const first = TWO_BLOCKS.indexOf('<<<<<<<');
  const second = TWO_BLOCKS.indexOf('<<<<<<<', first + 1);

  it('finds the first conflict after the caret', () => {
    expect(nextConflictOffset(TWO_BLOCKS, 0)).toBe(first);
    expect(nextConflictOffset(TWO_BLOCKS, first + 3)).toBe(second);
  });

  it('moves on from a caret parked at the start of a marker line', () => {
    expect(nextConflictOffset(TWO_BLOCKS, first)).toBe(second);
  });

  it('wraps to the first conflict past the last one', () => {
    expect(nextConflictOffset(TWO_BLOCKS, second)).toBe(first);
    expect(nextConflictOffset(TWO_BLOCKS, TWO_BLOCKS.length)).toBe(first);
  });

  it('answers -1 when no conflict is left', () => {
    expect(nextConflictOffset('kept\n>>>>>>> b\n', 0)).toBe(-1);
    expect(nextConflictOffset('', 0)).toBe(-1);
  });

  it('counts offsets in UTF-16 units, the way a textarea selection does', () => {
    const text = '😀 ж\n<<<<<<< a\n';

    expect(nextConflictOffset(text, 0)).toBe(5);
    expect(text.slice(5, 12)).toBe('<<<<<<<');
  });
});

describe('exceedsByteLimit', () => {
  it('lets a text of exactly the limit through and stops the byte after it', () => {
    expect(exceedsByteLimit('a'.repeat(10), 10)).toBe(false);
    expect(exceedsByteLimit('a'.repeat(11), 10)).toBe(true);
  });

  it('counts UTF-8 bytes, not the UTF-16 units a textarea reports', () => {
    // `é` is one unit and two bytes; an emoji two units and four bytes.
    expect(exceedsByteLimit('é'.repeat(5), 10)).toBe(false);
    expect(exceedsByteLimit('é'.repeat(6), 10)).toBe(true);
    expect(exceedsByteLimit('😀😀', 8)).toBe(false);
    expect(exceedsByteLimit('😀😀😀', 8)).toBe(true);
  });

});

describe('isEditableConflict and conflictKindNotice', () => {
  it('edits text that arrived with its content', () => {
    expect(isEditableConflict(conflict())).toBe(true);
    expect(isEditableConflict(conflict({ content: '' }))).toBe(true);
    expect(conflictKindNotice(conflict())).toBeNull();
  });

  it('never edits a text kind that came without content', () => {
    expect(isEditableConflict(conflict({ content: null }))).toBe(false);
  });

  it.each([
    ['binary', 'This is a binary file — pick a side.'],
    ['hologram', "This conflict can't be resolved here — use the terminal."],
  ])('explains why a %s conflict is not editable', (kind, notice) => {
    const shown = conflict({ kind, content: null, token: null });

    expect(isEditableConflict(shown)).toBe(false);
    expect(conflictKindNotice(shown)).toBe(notice);
  });
});

describe('canTakeSide and canStartOver', () => {
  it.each(['text', 'binary'])(
    'offers whole-file picks for a %s conflict',
    (kind) => {
      expect(canTakeSide(conflict({ kind }))).toBe(true);
    },
  );

  it.each(['submodule', 'hologram'])('offers none for a %s conflict', (kind) => {
    const shown = conflict({ kind, content: null });

    expect(canTakeSide(shown)).toBe(false);
    expect(takeChoices(shown)).toEqual([]);
  });

  it('starts over only on editable text both sides still have', () => {
    expect(canStartOver(conflict())).toBe(true);
    expect(canStartOver(conflict({ code: 'DU', ours: { label: 'origin/main', present: false } }))).toBe(false);
    expect(canStartOver(conflict({ code: 'UD', theirs: { label: 'x', present: false } }))).toBe(false);
    expect(canStartOver(conflict({ kind: 'binary', content: null }))).toBe(false);
  });
});

describe('takeChoices', () => {
  it('turns the side that deleted the file into the destructive pick', () => {
    const deletedByUs = conflict({
      path: 'docs/old.md',
      code: 'DU',
      ours: { label: 'origin/main', present: false },
    });

    expect(takeChoices(deletedByUs)).toEqual([
      {
        side: 'ours',
        label: 'Delete the file (origin/main)',
        title: 'Delete old.md?',
        body: 'The file is removed, as on origin/main, and the conflict is marked resolved.',
        confirm: 'Delete file',
        danger: true,
      },
      {
        side: 'theirs',
        label: 'Keep the file (fedcba9 Fix the login redirect)',
        title: 'Keep old.md?',
        body: 'The file stays as fedcba9 Fix the login redirect has it and is marked resolved.',
        confirm: 'Keep file',
        danger: false,
      },
    ]);
  });

  it('mirrors the picks when the other side deleted it', () => {
    const deletedByThem = conflict({ code: 'UD', theirs: { label: null, present: false } });

    expect(takeChoices(deletedByThem).map((c) => [c.side, c.label, c.danger])).toEqual([
      ['ours', 'Keep the file (origin/main)', false],
      ['theirs', 'Delete the file (Incoming change)', true],
    ]);
  });

  it('leaves a single way out when neither side has the file', () => {
    const bothDeleted = conflict({
      code: 'DD',
      kind: 'missing',
      content: null,
      ours: { label: 'origin/main', present: false },
      theirs: { label: 'x', present: false },
    });

    expect(takeChoices(bothDeleted).map((c) => [c.side, c.label, c.confirm, c.danger])).toEqual([
      ['ours', 'Delete the file', 'Delete file', true],
    ]);
  });
});

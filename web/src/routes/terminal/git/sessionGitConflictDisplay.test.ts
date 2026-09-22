import { describe, expect, it } from 'vitest';
import type { SessionGitConflict } from '../../../api/sessionGitRepository';
import {
  KEEP_EDITS_WARNING,
  START_OVER_LABEL,
  START_OVER_WITH_BASE_LABEL,
  canStartOver,
  canTakeSide,
  conflictCaption,
  conflictKindNotice,
  conflictLegend,
  conflictsLeftLabel,
  discardEditsPrompt,
  draftSavedNotice,
  exceedsByteLimit,
  fileChangedNotice,
  isEditableConflict,
  nextConflictOffset,
  resolvedElsewhereNotice,
  resolvedNotice,
  restorePrompt,
  restoredNotice,
  scanConflictText,
  sideName,
  sideRole,
  startOverPrompt,
  takeChoices,
  tooLargeToSaveNotice,
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
    ['a marker before a tab', '<<<<<<<\tlabel', true],
    ['a marker before a form feed', '<<<<<<<\fx', true],
    ['more than seven characters', '<<<<<<<<< label', true],
    ['six characters', '<<<<<< label', false],
    ['a label glued to the marker', '<<<<<<<label', false],
    ['an indented marker', ' <<<<<<< label', false],
    ['a marker mid-line', 'x <<<<<<< label', false],
    // JavaScript's `\s` takes these for whitespace; Python's bytes `\s` does not.
    ['a no-break space after the marker', '<<<<<<< label', false],
    ['a line separator after the marker', '<<<<<<< label', false],
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

  it('never stops an empty text', () => {
    expect(exceedsByteLimit('', 0)).toBe(false);
  });
});

describe('tooLargeToSaveNotice', () => {
  it("states the agent's limit in KB", () => {
    expect(tooLargeToSaveNotice(262144)).toBe('Too large to save here (over 256 KB).');
  });
});

describe('conflictsLeftLabel', () => {
  it('counts the open conflicts', () => {
    expect(conflictsLeftLabel(1, true)).toBe('1 conflict left');
    expect(conflictsLeftLabel(3, true)).toBe('3 conflicts left');
  });

  it('tells a stray end marker from a clean file', () => {
    expect(conflictsLeftLabel(0, true)).toBe('A conflict marker is left');
    expect(conflictsLeftLabel(0, false)).toBe('No conflict markers left');
  });
});

describe('sideName and sideRole', () => {
  it("names a side by the agent's label", () => {
    expect(sideName(conflict(), 'ours')).toBe('origin/main');
    expect(sideName(conflict(), 'theirs')).toBe('fedcba9 Fix the login redirect');
  });

  it('falls back to neutral names, never to ours or theirs', () => {
    const unlabelled = conflict({
      ours: { label: null, present: true },
      theirs: { label: '', present: true },
    });

    expect(sideName(unlabelled, 'ours')).toBe('Current branch');
    expect(sideName(unlabelled, 'theirs')).toBe('Incoming change');
  });

  it('explains a rebase the right way round: ours is the branch rebased onto', () => {
    expect(sideRole('rebasing', 'ours')).toBe(
      'the branch you are rebasing onto, plus your commits already replayed',
    );
    expect(sideRole('rebasing', 'theirs')).toBe('your commit being replayed');
  });

  it('explains a merge, and stays generic for any other operation', () => {
    expect(sideRole('merging', 'ours')).toBe('the branch you are on');
    expect(sideRole('merging', 'theirs')).toBe('the branch being merged in');
    expect(sideRole('cherry_picking', 'ours')).toBe('what you have checked out');
    expect(sideRole('clean', 'theirs')).toBe('the change being applied');
  });
});

describe('conflictCaption', () => {
  it.each([
    ['UU', 'Both sides changed this file.'],
    ['AA', 'Both sides added this file.'],
    ['DU', 'origin/main deleted this file; fedcba9 Fix the login redirect changed it.'],
    ['UD', 'origin/main changed this file; fedcba9 Fix the login redirect deleted it.'],
    ['DD', 'Both sides deleted this file.'],
    ['AU', 'Only origin/main has this file.'],
    ['UA', 'Only fedcba9 Fix the login redirect has this file.'],
    ['ZZ', 'The two sides disagree about this file.'],
  ])('reads %s in plain words', (code, caption) => {
    expect(conflictCaption(conflict({ code }))).toBe(caption);
  });
});

describe('conflictLegend', () => {
  it('maps the two regions of a block to the labelled sides', () => {
    expect(conflictLegend(conflict(), false)).toEqual([
      'Between <<<<<<< and =======: origin/main — the branch you are rebasing onto, plus your commits already replayed.',
      'Between ======= and >>>>>>>: fedcba9 Fix the login redirect — your commit being replayed.',
    ]);
  });

  it('adds the original lines when the text has base sections', () => {
    expect(conflictLegend(conflict({ state: 'merging' }), true)).toEqual([
      'Between <<<<<<< and |||||||: origin/main — the branch you are on.',
      'Between ||||||| and =======: the original lines, before either change.',
      'Between ======= and >>>>>>>: fedcba9 Fix the login redirect — the branch being merged in.',
    ]);
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
    ['too_large', 'This file is too large to edit here (over 256 KB). Pick a side, or resolve it in the terminal.'],
    ['binary', 'This is a binary file — pick a side.'],
    ['encoding', 'This file is not UTF-8 text — pick a side, or resolve it in the terminal.'],
    ['missing', 'The file is not in the working tree — pick a side.'],
    ['symlink', 'This is a symbolic link — pick a side.'],
    ['submodule', 'Submodule conflicts are resolved in the terminal.'],
    ['unsupported', "This conflict can't be resolved here — use the terminal."],
    ['hologram', "This conflict can't be resolved here — use the terminal."],
  ])('explains why a %s conflict is not editable', (kind, notice) => {
    const shown = conflict({ kind, content: null, token: null });

    expect(isEditableConflict(shown)).toBe(false);
    expect(conflictKindNotice(shown)).toBe(notice);
  });
});

describe('canTakeSide and canStartOver', () => {
  it.each(['text', 'too_large', 'binary', 'encoding', 'missing', 'symlink'])(
    'offers whole-file picks for a %s conflict',
    (kind) => {
      expect(canTakeSide(conflict({ kind }))).toBe(true);
    },
  );

  it.each(['submodule', 'unsupported', 'hologram'])('offers none for a %s conflict', (kind) => {
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
  it('offers either side by its label when both have the file', () => {
    expect(takeChoices(conflict())).toEqual([
      {
        side: 'ours',
        label: 'Use origin/main for the whole file',
        title: 'Use origin/main for the whole file?',
        body: 'Every conflict in login.py is settled with this side and the file is marked resolved. Edits made to it are discarded.',
        confirm: 'Use this side',
        danger: false,
      },
      {
        side: 'theirs',
        label: 'Use fedcba9 Fix the login redirect for the whole file',
        title: 'Use fedcba9 Fix the login redirect for the whole file?',
        body: 'Every conflict in login.py is settled with this side and the file is marked resolved. Edits made to it are discarded.',
        confirm: 'Use this side',
        danger: false,
      },
    ]);
  });

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

describe('prompts and notices', () => {
  it('names the file, not its directory, in the prompts', () => {
    expect(startOverPrompt('src/login.py')).toEqual({
      title: 'Start over?',
      body: 'login.py is rebuilt from both sides. Every edit made to it since the conflict — here or in the terminal — is discarded.',
    });
    expect(restorePrompt('src/login.py')).toEqual({
      title: 'Restore the conflict?',
      body: 'login.py goes back to its conflicted state. The resolution you applied is discarded.',
      confirm: 'Restore conflict',
    });
    expect(discardEditsPrompt('src/login.py')).toEqual({
      title: 'Discard your edits?',
      body: 'Your changes to login.py have not been saved.',
    });
    expect([START_OVER_LABEL, START_OVER_WITH_BASE_LABEL]).toEqual([
      'Start over',
      'Start over with original lines',
    ]);
  });

  it('reports each outcome with the whole path', () => {
    expect(resolvedNotice('src/login.py')).toBe('Resolved src/login.py.');
    expect(draftSavedNotice('src/login.py')).toBe('Saved src/login.py — the conflict is still open.');
    expect(restoredNotice('src/login.py')).toBe('Restored the conflict in src/login.py.');
  });

  it('explains a file that moved on, and what keeping the edits risks', () => {
    expect(fileChangedNotice('src/login.py')).toBe(
      'login.py changed on the agent machine since you opened it.',
    );
    expect(KEEP_EDITS_WARNING).toBe('Applying will overwrite the other change.');
    expect(resolvedElsewhereNotice('src/login.py')).toBe('login.py is no longer in conflict.');
  });
});

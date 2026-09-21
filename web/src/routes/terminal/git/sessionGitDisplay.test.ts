import { describe, expect, it } from 'vitest';
import { toApiError } from '../../../api/errors';
import type { GitItem } from '../../../api/gitRepository';
import {
  SessionGitError,
  toSessionGitError,
  type SessionGitDiff,
  type SessionGitEntry,
  type SessionGitHunk,
  type SessionGitJob,
  type SessionGitLine,
  type SessionGitRebaseInfo,
  type SessionGitRebaseResult,
  type SessionGitRepoInfo,
  type SessionGitStatus,
} from '../../../api/sessionGitRepository';
import type { GitCapabilities } from '../../../protocol/dto/agentVersion';
import {
  IDENTITY_FIX_COMMANDS,
  JOB_RESULT_DISMISS_MS,
  NETWORK_FAILURE_MESSAGE,
  authorLine,
  badgeFor,
  changeLabel,
  commitButtonLabel,
  commitNotice,
  composeCommitMessage,
  diffScopeFor,
  diffView,
  directory,
  discardPrompt,
  discardScopeFor,
  emptyDiffNotice,
  fileName,
  followUpLabel,
  followUpPrompt,
  followUpRequest,
  headLabel,
  hunkHeader,
  jobDoneTitle,
  jobErrorCopy,
  jobFollowUps,
  jobNotice,
  jobTitle,
  mutationErrorCopy,
  pathsFor,
  rebaseBanner,
  rebaseBlocker,
  rebaseErrorCopy,
  rebaseErrorOutput,
  rebaseOutcomeNotice,
  rebaseStepPrompt,
  rebaseTargets,
  refusalCopy,
  renderableText,
  repoStateLabel,
  rowActions,
  sectionAction,
  sectionsFor,
  selectionSubsets,
  sessionGitErrorCopy,
  shortRefName,
  showsContextChoices,
  showsJobOutput,
  splitCommitMessage,
  syncRequestFor,
  syncRows,
  truncatedDiffNotice,
  truncatedEntriesNotice,
  unavailableCopy,
  upstreamLabel,
} from './sessionGitDisplay';

const entry = (over: Partial<SessionGitEntry> = {}): SessionGitEntry => ({
  group: 'unstaged',
  path: 'src/app.py',
  orig_path: null,
  code: 'M',
  index_status: '.',
  worktree_status: 'M',
  rename_score: null,
  submodule: false,
  ...over,
});

/** A group only a newer agent would send; the union is the wire's today. */
const foreignGroup = (name: string): SessionGitEntry['group'] =>
  name as SessionGitEntry['group'];

/** A change kind only a newer agent would send. */
const foreignChange = (name: string): SessionGitDiff['change'] =>
  name as SessionGitDiff['change'];

const repo = (over: Partial<SessionGitRepoInfo> = {}): SessionGitRepoInfo => ({
  toplevel: '/srv/p1',
  git_dir: '/srv/p1/.git',
  common_dir: '/srv/p1/.git',
  head: { oid: 'abc1234def', short_oid: 'abc1234', branch: 'main', detached: false, initial: false },
  upstream: null,
  state: 'clean',
  is_linked_worktree: false,
  ...over,
});

const status = (over: Partial<SessionGitStatus> = {}): SessionGitStatus => ({
  session_id: 's1',
  cwd: '/srv/p1',
  etag: 'e1',
  unchanged: false,
  unavailable_reason: null,
  repo: repo(),
  entries: [],
  counts: { conflicts: 0, staged: 0, unstaged: 0, untracked: 0, total: 0 },
  truncated: false,
  entry_limit: 5000,
  ...over,
});

const line = (over: Partial<SessionGitLine> = {}): SessionGitLine => ({
  t: 'ctx',
  old: 1,
  new: 1,
  text: 'unchanged',
  no_newline: false,
  ...over,
});

const hunk = (over: Partial<SessionGitHunk> = {}): SessionGitHunk => ({
  old_start: 1,
  old_lines: 3,
  new_start: 1,
  new_lines: 4,
  heading: '',
  lines: [],
  ...over,
});

const diff = (over: Partial<SessionGitDiff> = {}): SessionGitDiff => ({
  path: 'src/app.py',
  orig_path: null,
  scope: 'unstaged',
  kind: 'text',
  change: 'modified',
  old_mode: '100644',
  new_mode: '100644',
  additions: 1,
  deletions: 0,
  hunks: [],
  truncated: false,
  total_lines: 0,
  line_limit: 5000,
  byte_limit: 1048576,
  ...over,
});

/** Off the wire, as `sessionGitRepository` builds them, not hand-assembled. */
const apiError = (httpStatus: number, body: unknown) =>
  toApiError(new Response(null, { status: httpStatus }), body);

const gitError = (code: string, message?: string): SessionGitError =>
  toSessionGitError(
    apiError(500, { detail: message === undefined ? { code } : { code, message } }),
  ) as SessionGitError;

describe('sectionsFor', () => {
  it('orders the known groups Conflicts, Staged, Changes, Untracked whatever the wire order', () => {
    const sections = sectionsFor(
      status({
        entries: [
          entry({ group: 'untracked', path: 'new.txt', code: '?' }),
          entry({ group: 'unstaged', path: 'b.py' }),
          entry({ group: 'staged', path: 'a.py', code: 'A' }),
          entry({ group: 'conflicts', path: 'c.py', code: 'UU' }),
        ],
      }),
    );

    expect(sections.map((section) => [section.group, section.title])).toEqual([
      ['conflicts', 'Conflicts'],
      ['staged', 'Staged'],
      ['unstaged', 'Changes'],
      ['untracked', 'Untracked'],
    ]);
  });

  it('omits an empty known group rather than listing a heading with no rows', () => {
    const sections = sectionsFor(status({ entries: [entry({ group: 'staged', code: 'A' })] }));

    expect(sections.map((section) => section.group)).toEqual(['staged']);
  });

  it('appends groups a newer agent adds, sorted, with underscores read as spaces', () => {
    const sections = sectionsFor(
      status({
        entries: [
          entry({ group: foreignGroup('zeta_group'), path: 'z' }),
          entry({ group: 'unstaged', path: 'm' }),
          entry({ group: foreignGroup('alpha'), path: 'a' }),
        ],
      }),
    );

    expect(sections.map((section) => [section.group, section.title])).toEqual([
      ['unstaged', 'Changes'],
      ['alpha', 'alpha'],
      ['zeta_group', 'zeta group'],
    ]);
  });

  it('keys rows by group, occurrence and path so a repeated path cannot collide', () => {
    // The agent decodes POSIX filenames lossily, so two files can arrive with
    // the same `path`; the occurrence goes first so no filename can spell
    // another row's key.
    const sections = sectionsFor(
      status({
        entries: [
          entry({ group: 'unstaged', path: 'same' }),
          entry({ group: 'unstaged', path: 'same' }),
          entry({ group: 'staged', path: 'same', code: 'A' }),
        ],
      }),
    );

    expect(sections.flatMap((section) => section.rows.map((row) => row.key))).toEqual([
      'staged:0:same',
      'unstaged:0:same',
      'unstaged:1:same',
    ]);
  });

  it('counts the occurrence per group and path, not the row\'s place in its section', () => {
    // Two different files in one section are each the first of their own
    // path: an index would make the second `unstaged:1:b.py`.
    const sections = sectionsFor(
      status({ entries: [entry({ path: 'a.py' }), entry({ path: 'b.py' })] }),
    );

    expect(sections.flatMap((section) => section.rows.map((row) => row.key))).toEqual([
      'unstaged:0:a.py',
      'unstaged:0:b.py',
    ]);
  });

  it('keeps a row\'s key when a file appears above it on a later poll', () => {
    // A key that renamed with the row's index would remount every later row
    // on each poll, and the selection with it.
    const before = sectionsFor(status({ entries: [entry({ path: 'b.py' })] }));
    const after = sectionsFor(
      status({ entries: [entry({ path: 'a.py' }), entry({ path: 'b.py' })] }),
    );

    const keyOf = (sections: ReturnType<typeof sectionsFor>, path: string): string | undefined =>
      sections.flatMap((section) => section.rows).find((row) => row.entry.path === path)?.key;
    expect(keyOf(after, 'b.py')).toBe(keyOf(before, 'b.py'));
    expect(keyOf(after, 'b.py')).toBe('unstaged:0:b.py');
  });

  it('returns no sections for a clean tree, entries absent or empty', () => {
    expect(sectionsFor(status({ entries: [] }))).toEqual([]);
    expect(sectionsFor(status({ entries: undefined }))).toEqual([]);
  });
});

describe('diffScopeFor', () => {
  it.each([
    ['staged', 'staged'],
    ['untracked', 'untracked'],
    ['unstaged', 'unstaged'],
    // Conflicted files are diffed against HEAD, which the agent serves as unstaged.
    ['conflicts', 'unstaged'],
  ] as const)('maps the %s group to the %s scope', (group, scope) => {
    expect(diffScopeFor(entry({ group }))).toBe(scope);
  });
});

describe('badgeFor', () => {
  it.each([
    [entry({ group: 'conflicts', code: 'UU' }), { label: 'UU', tone: 'conflict' }],
    [entry({ group: 'conflicts', code: '' }), { label: 'U', tone: 'conflict' }],
    [entry({ group: 'untracked', code: '?' }), { label: '?', tone: 'untracked' }],
    [entry({ group: 'staged', code: 'A' }), { label: 'A', tone: 'added' }],
    [entry({ code: 'D' }), { label: 'D', tone: 'deleted' }],
    [entry({ group: 'staged', code: 'R' }), { label: 'R', tone: 'renamed' }],
    [entry({ group: 'staged', code: 'C' }), { label: 'C', tone: 'renamed' }],
    [entry({ code: 'M' }), { label: 'M', tone: 'modified' }],
    [entry({ code: 'T' }), { label: 'T', tone: 'modified' }],
    [entry({ code: '' }), { label: 'M', tone: 'modified' }],
  ])('badges %o as %o', (row, badge) => {
    expect(badgeFor(row)).toEqual(badge);
  });
});

describe('path labels', () => {
  it('splits a nested path into name and directory', () => {
    expect(fileName('src/app.py')).toBe('app.py');
    expect(directory('src/app.py')).toBe('src');
    expect(directory('a/b/c.txt')).toBe('a/b');
  });

  it('reports no directory for a top-level file or a root-anchored one', () => {
    expect(fileName('README')).toBe('README');
    expect(directory('README')).toBeNull();
    expect(directory('/root')).toBeNull();
  });

  it('keeps the trailing slash on a nested repository git lists as a folder', () => {
    expect(fileName('mobaide_worktree/claude-session-56a3f247/')).toBe('claude-session-56a3f247/');
    expect(directory('mobaide_worktree/claude-session-56a3f247/')).toBe('mobaide_worktree');
    expect(fileName('vendor/')).toBe('vendor/');
    expect(directory('vendor/')).toBeNull();
  });
});

describe('repo header labels', () => {
  it('names the head by branch, then short oid, then HEAD', () => {
    expect(headLabel(repo())).toBe('main');
    expect(headLabel(repo({ head: { branch: null, short_oid: 'abc1234', detached: false, initial: false } }))).toBe('abc1234');
    expect(headLabel(repo({ head: { branch: null, short_oid: null, detached: false, initial: false } }))).toBe('HEAD');
  });

  it('flags a detached head and an unborn one', () => {
    expect(headLabel(repo({ head: { branch: null, short_oid: 'abc1234', detached: true, initial: false } }))).toBe('detached @ abc1234');
    expect(headLabel(repo({ head: { branch: null, short_oid: null, detached: true, initial: false } }))).toBe('detached @ HEAD');
    // Initial wins over everything: there is no commit to be detached from.
    expect(headLabel(repo({ head: { branch: 'main', short_oid: null, detached: true, initial: true } }))).toBe('No commits yet');
  });

  it('counts ahead and behind, or reports the upstream gone', () => {
    expect(upstreamLabel(null)).toBeNull();
    expect(upstreamLabel(undefined)).toBeNull();
    expect(upstreamLabel({ ref: 'origin/main', ahead: 1, behind: 0 })).toBe('↑1 ↓0 · origin/main');
    expect(upstreamLabel({ ref: 'origin/main', ahead: null, behind: null })).toBe('origin/main · upstream gone');
    expect(upstreamLabel({ ref: 'origin/main', ahead: 2 })).toBe('origin/main · upstream gone');
  });

  it('labels an in-progress operation and nothing for a clean state', () => {
    expect(repoStateLabel('clean')).toBeNull();
    expect(repoStateLabel('cherry_picking')).toBe('cherry-picking');
    expect(repoStateLabel('merging')).toBe('merging');
    expect(repoStateLabel('some_new_state')).toBe('some new state');
  });
});

describe('unavailableCopy', () => {
  it.each([
    ['not_a_repo', 'Not a git repository'],
    ['bare_repository', 'Bare repository'],
    ['cwd_missing', 'Folder not found'],
    ['unsafe_directory', 'Unsafe directory'],
    ['git_too_old', 'Git is too old'],
  ])('titles %s as "%s"', (reason, title) => {
    expect(unavailableCopy(reason).title).toBe(title);
    expect(unavailableCopy(reason).body).not.toBe('');
  });

  it('names an unknown reason in the generic fallback', () => {
    expect(unavailableCopy('locked')).toEqual({
      title: 'Changes unavailable',
      body: 'The agent could not read this repository (locked).',
    });
  });
});

describe('sessionGitErrorCopy', () => {
  it('tells the two 404s apart', () => {
    const outdated = toSessionGitError(apiError(404, { detail: 'Not Found' }));
    const gone = toSessionGitError(apiError(404, { detail: "Session 's1' not found" }));

    expect(sessionGitErrorCopy(outdated)).toBe('Update your agent to view changes.');
    expect(sessionGitErrorCopy(gone)).toBe('This session has ended.');
  });

  it('phrases each git failure code', () => {
    expect(sessionGitErrorCopy(gitError('git_unavailable'))).toBe(
      'git is not installed on the agent host.',
    );
    expect(sessionGitErrorCopy(gitError('git_timeout'))).toBe(
      'git took too long to answer. Try again.',
    );
    expect(sessionGitErrorCopy(gitError('git_error'))).toBe('git failed unexpectedly.');
    expect(sessionGitErrorCopy(gitError('git_error', 'fatal: bad object'))).toBe(
      'git failed: fatal: bad object',
    );
  });

  it('reuses the unavailable body for a reason that arrives as an error code', () => {
    expect(sessionGitErrorCopy(gitError('unsafe_directory'))).toBe(
      unavailableCopy('unsafe_directory').body,
    );
    expect(sessionGitErrorCopy(gitError('rate_limited'))).toBe(
      'git refused the request (rate_limited).',
    );
  });

  it('falls back to the relay detail, the status, the network, then the message', () => {
    expect(sessionGitErrorCopy(apiError(503, { detail: 'Agent is offline' }))).toBe(
      'Agent is offline',
    );
    expect(sessionGitErrorCopy(apiError(500, { detail: { error: 'boom' } }))).toBe(
      'Request failed (500)',
    );
    // FastAPI's 422 is a list of `msg`s, which `toApiError` has already folded.
    expect(
      sessionGitErrorCopy(
        apiError(422, { detail: [{ loc: ['query', 'context'], msg: 'bad', type: 'x' }] }),
      ),
    ).toBe('bad');
    // openapi-fetch rethrows what `fetch` threw; a refused connection is a TypeError.
    expect(sessionGitErrorCopy(new TypeError('Failed to fetch'))).toBe(NETWORK_FAILURE_MESSAGE);
    expect(sessionGitErrorCopy(new Error('parse failed'))).toBe('parse failed');
    expect(sessionGitErrorCopy(new Error(''))).toBe('Something went wrong.');
    expect(sessionGitErrorCopy(undefined)).toBe('Something went wrong.');
  });
});

describe('truncatedEntriesNotice', () => {
  it('says nothing for a complete list', () => {
    expect(truncatedEntriesNotice(status({ entries: [entry()] }))).toBeNull();
  });

  it('gives the shown count against a lower-bound total when the counts exceed it', () => {
    const cut = status({
      truncated: true,
      entries: [entry({ path: 'a' }), entry({ path: 'b' })],
      counts: { conflicts: 0, staged: 0, unstaged: 10, untracked: 0, total: 10 },
    });

    expect(truncatedEntriesNotice(cut)).toBe('Showing the first 2 of at least 10 entries.');
  });

  it('reports only what arrived when the byte cap hit before the counts could', () => {
    // The counts come from the same capped parse, so they can equal the shown
    // figure; the notice then claims neither a total nor the entry limit.
    const cut = status({
      truncated: true,
      entries: [entry({ path: 'a' }), entry({ path: 'b' })],
      counts: { conflicts: 0, staged: 0, unstaged: 2, untracked: 0, total: 2 },
      entry_limit: 5000,
    });

    expect(truncatedEntriesNotice(cut)).toBe('List cut short after 2 entries.');
    expect(truncatedEntriesNotice(status({ truncated: true, counts: null }))).toBe(
      'List cut short after 0 entries.',
    );
    expect(truncatedEntriesNotice(cut)).not.toContain('5000');
  });
});

describe('diff text', () => {
  it('reads a change kind as words', () => {
    expect(changeLabel('type_changed')).toBe('type changed');
    expect(changeLabel('renamed')).toBe('renamed');
  });

  it('widens tabs to four spaces and makes a carriage return visible', () => {
    expect(renderableText('\tif x:\r')).toBe('    if x:␍');
    expect(renderableText('plain')).toBe('plain');
  });

  it('formats a hunk header with its heading only when there is one', () => {
    expect(hunkHeader(hunk())).toBe('@@ -1,3 +1,4 @@');
    expect(hunkHeader(hunk({ heading: '   ' }))).toBe('@@ -1,3 +1,4 @@');
    expect(hunkHeader(hunk({ old_start: 10, old_lines: 2, new_start: 12, new_lines: 5, heading: 'def main():' }))).toBe(
      '@@ -10,2 +12,5 @@  def main():',
    );
  });
});

describe('showsContextChoices', () => {
  const hunks = [hunk({ lines: [line(), line({ t: 'add', old: null, new: 2, text: 'new' })] })];

  it.each(['modified', 'renamed', 'copied'] as const)(
    'offers the choice for a %s text diff with hunks',
    (change) => {
      expect(showsContextChoices(diff({ change, hunks }))).toBe(true);
    },
  );

  it.each(['added', 'deleted', 'type_changed'] as const)(
    'hides it for a %s file, which has no unchanged lines to widen',
    (change) => {
      expect(showsContextChoices(diff({ change, hunks }))).toBe(false);
    },
  );

  it.each(['binary', 'submodule', 'too_large', 'missing'] as const)(
    'hides it for a %s diff, which the context cannot change',
    (kind) => {
      // A submodule arrives with its pointer lines as a hunk; still no context.
      expect(showsContextChoices(diff({ kind, hunks }))).toBe(false);
    },
  );

  it('hides it for a text diff with no hunks: a pure rename or a mode-only change', () => {
    expect(showsContextChoices(diff({ change: 'renamed', hunks: [] }))).toBe(false);
    expect(
      showsContextChoices(diff({ old_mode: '100644', new_mode: '100755', hunks: undefined })),
    ).toBe(false);
  });

  it('keeps it for a change the agent left out or a newer agent names', () => {
    expect(showsContextChoices(diff({ change: null, hunks }))).toBe(true);
    expect(showsContextChoices(diff({ change: undefined, hunks }))).toBe(true);
    expect(showsContextChoices(diff({ change: foreignChange('merged'), hunks }))).toBe(true);
  });
});

describe('diffView', () => {
  it('replaces the hunks with a notice for a binary, oversized or missing file', () => {
    expect(diffView(diff({ kind: 'binary' }))).toEqual({
      hunks: [],
      notice: 'Binary file — no preview',
      truncated: null,
      gutterDigits: 2,
    });
    expect(diffView(diff({ kind: 'too_large', byte_limit: 2 * 1024 * 1024 })).notice).toBe(
      'File too large to preview (2 MiB limit)',
    );
    expect(diffView(diff({ kind: 'missing' })).notice).toBe('Nothing to show for this file');
  });

  it('lays each line out with both gutters, a rendered text and the no-newline flag', () => {
    const view = diffView(
      diff({
        hunks: [
          hunk({
            heading: 'def main():',
            lines: [
              line({ t: 'ctx', old: 1, new: 1, text: 'a' }),
              line({ t: 'del', old: 2, new: null, text: '\tb' }),
              line({ t: 'add', old: null, new: 2, text: 'c\r', no_newline: true }),
            ],
          }),
        ],
      }),
    );

    expect(view.notice).toBeNull();
    expect(view.truncated).toBeNull();
    expect(view.hunks).toEqual([
      {
        key: 'h0',
        header: '@@ -1,3 +1,4 @@  def main():',
        lines: [
          { key: 'h0:l0', t: 'ctx', old: 1, new: 1, text: 'a', noNewline: false },
          { key: 'h0:l1', t: 'del', old: 2, new: null, text: '    b', noNewline: false },
          { key: 'h0:l2', t: 'add', old: null, new: 2, text: 'c␍', noNewline: true },
        ],
      },
    ]);
  });

  it('sizes the gutters to the widest line number, never under two digits', () => {
    expect(diffView(diff({ hunks: [hunk({ lines: [line()] })] })).gutterDigits).toBe(2);
    expect(
      diffView(
        diff({
          hunks: [
            hunk({ old_start: 1, old_lines: 3, new_start: 1, new_lines: 3, lines: [line()] }),
            hunk({ old_start: 98, old_lines: 5, new_start: 120, new_lines: 5, lines: [line()] }),
          ],
        }),
      ).gutterDigits,
    ).toBe(3);
  });

  it('explains an empty text diff by the mode change, else as no text changes', () => {
    expect(diffView(diff({ old_mode: '100644', new_mode: '100755' })).notice).toBe(
      'Mode changed only: 100644 → 100755',
    );
    expect(diffView(diff()).notice).toBe('No text changes');
    expect(diffView(diff({ old_mode: null, new_mode: '100755' })).notice).toBe('No text changes');
    expect(emptyDiffNotice(diff({ kind: 'submodule', old_mode: null, new_mode: null }))).toBe(
      'No text changes',
    );
  });

  it('keeps a header-only hunk in a complete diff', () => {
    // An empty hunk in a diff git finished is content (a pure mode change
    // still emits its range); only a truncated diff treats it as overflow.
    const view = diffView(diff({ hunks: [hunk()] }));

    expect(view.hunks.map((row) => row.header)).toEqual(['@@ -1,3 +1,4 @@']);
    expect(view.notice).toBeNull();
  });

  it('drops the header-only tail of a truncated diff and counts only what is shown', () => {
    const view = diffView(
      diff({
        truncated: true,
        total_lines: 5000,
        hunks: [hunk({ lines: [line(), line({ old: 2, new: 2 })] }), hunk({ old_start: 400 })],
      }),
    );

    expect(view.hunks.map((row) => row.key)).toEqual(['h0']);
    expect(view.truncated).toBe('Showing the first 2 of at least 5000 lines');
    // Not the empty-diff notice: nothing shown is not the same as nothing there.
    expect(view.notice).toBeNull();
  });

  it('never claims an exact total once the byte cap has hidden the rest', () => {
    // `total_lines` is a lower bound when the agent's byte cap cut git off, so
    // a total that only matches what arrived is not presented as the size.
    expect(truncatedDiffNotice(40, 40)).toBe('Truncated after 40 lines — the full diff is longer');
    expect(truncatedDiffNotice(40, 41)).toBe('Showing the first 40 of at least 41 lines');
    expect(
      diffView(diff({ truncated: true, total_lines: 1, hunks: [hunk({ lines: [line()] })] }))
        .truncated,
    ).toBe('Truncated after 1 lines — the full diff is longer');
  });
});

const ALL_GIT: GitCapabilities = {
  stage: true,
  discard: true,
  commit: true,
  fetch: true,
  pull: true,
  push: true,
  branches: true,
  rebase: true,
  resolve: true,
  deleteRemote: true,
};
const NO_ACTIONS = { stage: false, unstage: false, discard: false, resolve: false };

const staged = (over: Partial<SessionGitEntry> = {}): SessionGitEntry =>
  entry({ group: 'staged', index_status: 'M', worktree_status: '.', ...over });
const untracked = (path: string): SessionGitEntry =>
  entry({ group: 'untracked', path, code: '?', index_status: '?', worktree_status: '?' });
const conflict = (path: string): SessionGitEntry =>
  entry({ group: 'conflicts', path, code: 'UU', index_status: 'U', worktree_status: 'U' });

/** The rows the list would build for these entries, flattened in display order. */
const rowsOf = (...entries: SessionGitEntry[]) =>
  sectionsFor(status({ entries })).flatMap((section) => section.rows);

describe('refusalCopy', () => {
  it('phrases each 409 code the mutating routes answer', () => {
    expect(refusalCopy('identity_required', null)).toBe(
      "Git doesn't know who you are on the agent machine.",
    );
    expect(refusalCopy('unmerged', null)).toBe('Resolve the conflicts first.');
    expect(refusalCopy('nothing_to_commit', null)).toBe('Nothing is staged.');
    expect(refusalCopy('no_head', null)).toBe('There is no commit to amend yet.');
    expect(refusalCopy('hook_failed', 'lint: nope')).toBe('A commit hook rejected the commit.');
    expect(refusalCopy('index_locked', null)).toBe('Another git process is running — try again.');
    expect(refusalCopy('pathspec_mismatch', null)).toBe('That file changed under you — refreshed.');
    expect(refusalCopy('empty_message', null)).toBe('Enter a commit summary.');
  });

  it('names the file that still holds conflict markers', () => {
    expect(refusalCopy('conflict_markers', 'src/login.py')).toBe(
      'src/login.py still has conflict markers — finish resolving it first.',
    );
    expect(refusalCopy('conflict_markers', null)).toBe(
      'The file still has conflict markers — finish resolving it first.',
    );
  });

  it('names an unknown code, with the git output when there is any', () => {
    expect(refusalCopy('rate_limited', null)).toBe('git refused the request (rate_limited).');
    expect(refusalCopy('rate_limited', 'slow down')).toBe(
      'git refused the request (rate_limited): slow down',
    );
  });
});

describe('mutationErrorCopy', () => {
  it('reads git_too_old as the 2.26 floor of the mutating routes, not the 2.15 of the polls', () => {
    expect(mutationErrorCopy(gitError('git_too_old'))).toBe(
      'Staging needs git 2.26 or newer on the agent machine.',
    );
    expect(sessionGitErrorCopy(gitError('git_too_old'))).toBe(unavailableCopy('git_too_old').body);
  });

  it('defers to the viewer copy for everything else', () => {
    expect(mutationErrorCopy(gitError('index_locked'))).toBe(
      'Another git process is running — try again.',
    );
    expect(mutationErrorCopy(gitError('git_timeout'))).toBe('git took too long to answer. Try again.');
    expect(mutationErrorCopy(new TypeError('Failed to fetch'))).toBe(NETWORK_FAILURE_MESSAGE);
  });

  it('ships the host-side identity fix verbatim, ready to paste', () => {
    expect(IDENTITY_FIX_COMMANDS).toEqual([
      'git config --global user.name "Your Name"',
      'git config --global user.email "you@example.com"',
    ]);
  });
});

describe('rowActions', () => {
  it('unstages a staged row, stages or discards a modified or untracked one, and marks a conflict resolved', () => {
    expect(rowActions(staged(), ALL_GIT)).toEqual({ ...NO_ACTIONS, unstage: true });
    expect(rowActions(entry(), ALL_GIT)).toEqual({ ...NO_ACTIONS, stage: true, discard: true });
    expect(rowActions(untracked('n.txt'), ALL_GIT)).toEqual({
      ...NO_ACTIONS,
      stage: true,
      discard: true,
    });
    expect(rowActions(conflict('c.py'), ALL_GIT)).toEqual({ ...NO_ACTIONS, resolve: true });
    expect(rowActions(entry({ group: foreignGroup('ignored') }), ALL_GIT)).toEqual(NO_ACTIONS);
  });

  it('gates each action on its own capability', () => {
    const discardOnly: GitCapabilities = {
      stage: false,
      discard: true,
      commit: true,
      fetch: false,
      pull: false,
      push: false,
      branches: false,
      rebase: false,
      resolve: false,
      deleteRemote: false,
    };
    const stageOnly: GitCapabilities = {
      stage: true,
      discard: false,
      commit: false,
      fetch: false,
      pull: false,
      push: false,
      branches: false,
      rebase: false,
      resolve: false,
      deleteRemote: false,
    };

    expect(rowActions(entry(), discardOnly)).toEqual({ ...NO_ACTIONS, discard: true });
    expect(rowActions(staged(), discardOnly)).toEqual(NO_ACTIONS);
    expect(rowActions(entry(), stageOnly)).toEqual({ ...NO_ACTIONS, stage: true });
    expect(rowActions(staged(), stageOnly)).toEqual({ ...NO_ACTIONS, unstage: true });
  });

  it('offers Mark resolved only on an agent that both stages and guards against leftover markers', () => {
    expect(rowActions(conflict('c.py'), { ...ALL_GIT, resolve: false })).toEqual(NO_ACTIONS);
    expect(rowActions(conflict('c.py'), { ...ALL_GIT, stage: false })).toEqual(NO_ACTIONS);
    expect(rowActions(entry(), { ...ALL_GIT, stage: false }).resolve).toBe(false);
    expect(rowActions(staged(), ALL_GIT).resolve).toBe(false);
  });

  it('keeps conflicts out of the bulk selection even when they can be marked resolved', () => {
    const sections = sectionsFor(status({ entries: [conflict('a.py'), conflict('b.py')] }));
    const everything = new Set(sections.flatMap((section) => section.rows.map((row) => row.key)));

    const subsets = selectionSubsets(sections, everything, ALL_GIT);

    expect(subsets).toEqual({ stage: [], unstage: [], discard: [] });
    expect(sectionAction(sections[0]!, ALL_GIT)).toBeNull();
  });
});

describe('discardScopeFor', () => {
  it('sends an untracked row to clean and a tracked one to restore', () => {
    expect(discardScopeFor(untracked('n.txt'))).toBe('untracked');
    expect(discardScopeFor(entry())).toBe('unstaged');
    expect(discardScopeFor(staged())).toBe('unstaged');
  });
});

describe('pathsFor', () => {
  it('names both sides of a rename, each path once', () => {
    const rows = rowsOf(
      staged({ code: 'R', index_status: 'R', orig_path: 'src/old.py', rename_score: 90 }),
      entry({ path: 'src/old.py' }),
      untracked('notes.txt'),
    );

    expect(pathsFor(rows)).toEqual(['src/app.py', 'src/old.py', 'notes.txt']);
  });

  it('is empty for no rows', () => {
    expect(pathsFor([])).toEqual([]);
  });
});

describe('selectionSubsets', () => {
  const sections = sectionsFor(
    status({
      entries: [
        staged({ path: 's.py' }),
        entry({ path: 'm.py' }),
        untracked('u.txt'),
        conflict('c.py'),
      ],
    }),
  );
  const paths = (rows: { entry: SessionGitEntry }[]): string[] => rows.map((row) => row.entry.path);

  it('sorts the ticked rows by the action each can take', () => {
    const everything = new Set(sections.flatMap((section) => section.rows.map((row) => row.key)));

    const subsets = selectionSubsets(sections, everything, ALL_GIT);

    expect(paths(subsets.stage)).toEqual(['m.py', 'u.txt']);
    expect(paths(subsets.unstage)).toEqual(['s.py']);
    expect(paths(subsets.discard)).toEqual(['m.py', 'u.txt']);
  });

  it('ignores rows that are not ticked and actions the agent lacks', () => {
    const onlyModified = new Set(['unstaged:0:m.py']);

    const subsets = selectionSubsets(sections, onlyModified, { ...ALL_GIT, discard: false });

    expect(paths(subsets.stage)).toEqual(['m.py']);
    expect(subsets.unstage).toEqual([]);
    expect(subsets.discard).toEqual([]);
  });
});

describe('sectionAction', () => {
  it('offers Unstage all on Staged, Stage all on Changes and Untracked, nothing elsewhere', () => {
    const sections = sectionsFor(
      status({
        entries: [
          conflict('c.py'),
          staged({ path: 's.py' }),
          entry({ path: 'm.py' }),
          untracked('u.txt'),
          entry({ group: foreignGroup('ignored'), path: 'i.txt' }),
        ],
      }),
    );

    const byGroup = Object.fromEntries(
      sections.map((section) => [section.group, sectionAction(section, ALL_GIT)]),
    );

    expect(byGroup).toEqual({
      conflicts: null,
      staged: { label: 'Unstage all', action: 'unstage' },
      unstaged: { label: 'Stage all', action: 'stage' },
      untracked: { label: 'Stage all', action: 'stage' },
      ignored: null,
    });
  });

  it('offers nothing without the stage capability', () => {
    const [section] = sectionsFor(status({ entries: [staged()] }));
    if (section === undefined) throw new Error('no section');

    expect(
      sectionAction(section, {
        stage: false,
        discard: true,
        commit: true,
        fetch: false,
        pull: false,
        push: false,
        branches: false,
        rebase: false,
        resolve: false,
        deleteRemote: false,
      }),
    ).toBeNull();
  });
});

describe('discardPrompt', () => {
  it('names the one file and says whether it is deleted or reverted', () => {
    expect(discardPrompt(rowsOf(untracked('notes.txt')))).toEqual({
      title: 'Delete untracked file?',
      body: 'notes.txt is not tracked by git and will be deleted. This cannot be undone.',
    });
    expect(discardPrompt(rowsOf(entry()))).toEqual({
      title: 'Discard changes?',
      body: 'Changes to src/app.py will be thrown away. This cannot be undone.',
    });
  });

  it('counts a multi-file discard', () => {
    expect(discardPrompt(rowsOf(entry(), untracked('notes.txt')))).toEqual({
      title: 'Discard 2 files?',
      body:
        'Their working-tree changes will be thrown away and untracked ones deleted. ' +
        'This cannot be undone.',
    });
  });
});

describe('commit copy', () => {
  it('composes subject and body the way git commit -F reads them', () => {
    expect(composeCommitMessage('  Fix it  ', '')).toBe('Fix it');
    expect(composeCommitMessage('Fix it', '  \n')).toBe('Fix it');
    expect(composeCommitMessage('Fix it', '  Why.\n')).toBe('Fix it\n\nWhy.');
  });

  it('splits the previous commit back into the two fields', () => {
    expect(
      splitCommitMessage({ oid: 'abc', short_oid: 'abc', subject: 'Fix it', body: 'Why.' }),
    ).toEqual({ summary: 'Fix it', description: 'Why.' });
  });

  it('labels the button by the staged count, or as an amend', () => {
    expect(commitButtonLabel(0, false)).toBe('Commit 0 files');
    expect(commitButtonLabel(1, false)).toBe('Commit 1 file');
    expect(commitButtonLabel(3, false)).toBe('Commit 3 files');
    expect(commitButtonLabel(3, true)).toBe('Amend commit');
  });

  it('phrases the result notice and the author line', () => {
    expect(commitNotice({ oid: 'abc1234def', short_oid: 'abc1234', subject: 'Fix it' })).toBe(
      'Committed abc1234 · Fix it',
    );
    expect(authorLine({ name: 'Ada Lovelace', email: 'ada@example.com' })).toBe(
      'Committing as Ada Lovelace <ada@example.com>',
    );
  });
});

const NO_SYNC: GitCapabilities = {
  stage: true,
  discard: true,
  commit: true,
  fetch: false,
  pull: false,
  push: false,
  branches: false,
  rebase: false,
  resolve: false,
  deleteRemote: false,
};

const job = (over: Partial<SessionGitJob> = {}): SessionGitJob => ({
  job_id: 'j1',
  session_id: 's1',
  op: 'push',
  status: 'running',
  phase: null,
  percent: null,
  line: null,
  ok: null,
  error_code: null,
  message: null,
  ...over,
});
const doneJob = (over: Partial<SessionGitJob> = {}): SessionGitJob =>
  job({ status: 'done', ok: true, message: 'main -> main', ...over });
const failedJob = (code: string, over: Partial<SessionGitJob> = {}): SessionGitJob =>
  job({ status: 'done', ok: false, error_code: code, ...over });
const tracking = (ahead: number, behind: number): SessionGitRepoInfo =>
  repo({ upstream: { ref: 'origin/main', ahead, behind } });

describe('syncRows', () => {
  const pull = (info: SessionGitRepoInfo) => syncRows(info, ALL_GIT).find((row) => row.op === 'pull');
  const push = (info: SessionGitRepoInfo) => syncRows(info, ALL_GIT).find((row) => row.op === 'push');

  it('offers a row only for the capabilities the agent advertises', () => {
    expect(syncRows(tracking(1, 1), NO_SYNC)).toEqual([]);
    expect(syncRows(tracking(1, 1), { ...NO_SYNC, fetch: true }).map((row) => row.op)).toEqual([
      'fetch',
    ]);
    expect(syncRows(tracking(1, 1), ALL_GIT).map((row) => row.op)).toEqual(['fetch', 'pull', 'push']);
  });

  it('always lets a fetch through', () => {
    expect(syncRows(repo(), ALL_GIT)[0]).toEqual({
      op: 'fetch',
      label: 'Fetch',
      detail: 'Update remote-tracking branches',
      enabled: true,
    });
  });

  it('enables Pull only with commits behind, and says why not otherwise', () => {
    expect(pull(tracking(0, 2))).toEqual({
      op: 'pull',
      label: 'Pull',
      detail: 'Fast-forward 2 commits from origin/main',
      enabled: true,
    });
    expect(pull(tracking(0, 1))).toMatchObject({
      detail: 'Fast-forward 1 commit from origin/main',
      enabled: true,
    });
    expect(pull(tracking(3, 0))).toMatchObject({ detail: 'Nothing to pull', enabled: false });
    expect(pull(repo())).toMatchObject({ detail: 'No upstream', enabled: false });
    expect(pull(repo({ upstream: { ref: 'origin/main', ahead: null, behind: null } }))).toMatchObject({
      detail: 'Upstream gone',
      enabled: false,
    });
  });

  it('offers Publish branch without an upstream and Push only with commits ahead', () => {
    expect(push(repo())).toEqual({
      op: 'push',
      label: 'Publish branch',
      detail: 'Push and set the upstream',
      enabled: true,
    });
    expect(push(tracking(3, 0))).toEqual({
      op: 'push',
      label: 'Push',
      detail: '3 commits to origin/main',
      enabled: true,
    });
    expect(push(tracking(1, 2))).toMatchObject({ detail: '1 commit to origin/main', enabled: true });
    expect(push(tracking(0, 2))).toMatchObject({
      label: 'Push',
      detail: 'Nothing to push',
      enabled: false,
    });
    expect(push(repo({ upstream: { ref: 'origin/main', ahead: null, behind: null } }))).toMatchObject({
      detail: 'Recreate origin/main',
      enabled: true,
    });
  });

  it('blocks Pull and Push, never Fetch, on a detached or unborn head and without a repo', () => {
    const head = repo().head;
    const detached = repo({ head: { ...head, branch: null, detached: true } });
    const initial = repo({ head: { ...head, initial: true } });

    expect(syncRows(detached, ALL_GIT).map((row) => [row.op, row.enabled, row.detail])).toEqual([
      ['fetch', true, 'Update remote-tracking branches'],
      ['pull', false, 'Detached HEAD'],
      ['push', false, 'Detached HEAD'],
    ]);
    expect(syncRows(initial, ALL_GIT).slice(1).map((row) => row.detail)).toEqual([
      'No commits yet',
      'No commits yet',
    ]);
    expect(syncRows(null, ALL_GIT).map((row) => [row.enabled, row.detail])).toEqual([
      [true, 'Update remote-tracking branches'],
      [false, 'Repository unavailable'],
      [false, 'Repository unavailable'],
    ]);
  });
});

describe('sync requests', () => {
  it('starts each row with its safe default', () => {
    expect(syncRequestFor('fetch')).toEqual({ op: 'fetch' });
    expect(syncRequestFor('pull')).toEqual({ op: 'pull', mode: 'ff_only' });
    expect(syncRequestFor('push')).toEqual({ op: 'push', force: false });
  });

  it('maps each follow-up to the request that rewrites history', () => {
    expect(followUpRequest('pull_rebase')).toEqual({ op: 'pull', mode: 'rebase' });
    expect(followUpRequest('pull_merge')).toEqual({ op: 'pull', mode: 'merge' });
    expect(followUpRequest('force_push')).toEqual({ op: 'push', force: true });
  });
});

describe('job copy', () => {
  it('titles a running and a finished job per op', () => {
    expect((['fetch', 'pull', 'push'] as const).map(jobTitle)).toEqual([
      'Fetching…',
      'Pulling…',
      'Pushing…',
    ]);
    expect(jobDoneTitle('fetch', true)).toBe('Fetched');
    expect(jobDoneTitle('pull', true)).toBe('Pulled');
    expect(jobDoneTitle('push', true)).toBe('Pushed');
    expect(jobDoneTitle('fetch', false)).toBe('Fetch failed');
    expect(jobDoneTitle('pull', false)).toBe('Pull failed');
    expect(jobDoneTitle('push', false)).toBe('Push failed');
  });

  it('phrases every job error code identically to the mobile clients', () => {
    expect(jobErrorCopy('cancelled', null)).toBe('Cancelled.');
    expect(jobErrorCopy('git_timeout', null)).toBe('git took too long to answer. Try again.');
    expect(jobErrorCopy('auth_required', null)).toBe(
      'No credentials for this remote. Configure a credential helper or SSH agent on the agent host.',
    );
    expect(jobErrorCopy('network', null)).toBe("Couldn't reach the remote.");
    expect(jobErrorCopy('remote_not_found', null)).toBe("The remote doesn't exist.");
    expect(jobErrorCopy('rejected', null)).toBe('The remote rejected the push.');
    expect(jobErrorCopy('not_fast_forward', null)).toBe('Your branch is behind the remote.');
    expect(jobErrorCopy('conflicts', null)).toBe('Stopped on conflicts — resolve them in the terminal.');
    expect(jobErrorCopy('dirty_worktree', null)).toBe('Commit or stash your changes first.');
    expect(jobErrorCopy('unmerged', null)).toBe('Resolve the conflicts first.');
    expect(jobErrorCopy('operation_in_progress', null)).toBe(
      'A rebase or merge is already in progress.',
    );
    expect(jobErrorCopy('no_upstream', null)).toBe('This branch has no upstream.');
    expect(jobErrorCopy('no_remote', null)).toBe('This repository has no remote.');
    expect(jobErrorCopy('detached_head', null)).toBe('Detached HEAD — check out a branch first.');
    expect(jobErrorCopy('no_head', null)).toBe('No commits yet.');
    expect(jobErrorCopy('hook_failed', null)).toBe('A pre-push hook rejected the push.');
    expect(jobErrorCopy('index_locked', null)).toBe('Another git process is running — try again.');
  });

  it('falls back to git_error and unknown codes on the message, else a plain failure', () => {
    expect(jobErrorCopy('git_error', 'fatal: boom')).toBe('fatal: boom');
    expect(jobErrorCopy('git_error', null)).toBe('git failed.');
    expect(jobErrorCopy('something_new', 'details')).toBe('details');
    expect(jobErrorCopy('something_new', '')).toBe('git failed.');
    expect(jobErrorCopy(null, undefined)).toBe('git failed.');
  });

  it('builds the banner line from the title, the summary or the failure copy', () => {
    expect(jobNotice(job())).toBe('Pushing…');
    expect(jobNotice(job({ op: 'fetch', phase: 'Receiving objects', percent: 12 }))).toBe(
      'Fetching…',
    );
    expect(jobNotice(doneJob())).toBe('Pushed · main -> main');
    expect(jobNotice(doneJob({ message: null }))).toBe('Pushed');
    expect(jobNotice(doneJob({ op: 'pull', message: '' }))).toBe('Pulled');
    expect(
      jobNotice(failedJob('rejected', { message: '! [rejected] main -> main (fetch first)' })),
    ).toBe('Push failed: The remote rejected the push.');
    expect(jobNotice(failedJob('git_error', { op: 'fetch', message: 'fatal: boom' }))).toBe(
      'Fetch failed: fatal: boom',
    );
  });

  it("shows git's output beside a failure only when the copy did not already say it", () => {
    expect(showsJobOutput(failedJob('rejected', { message: '! [rejected] main -> main' }))).toBe(true);
    expect(showsJobOutput(failedJob('git_error', { message: 'fatal: boom' }))).toBe(false);
    expect(showsJobOutput(failedJob('cancelled', { message: 'cancelled' }))).toBe(true);
    expect(showsJobOutput(failedJob('no_remote', { message: null }))).toBe(false);
    expect(showsJobOutput(doneJob())).toBe(false);
    expect(showsJobOutput(job({ message: 'x' }))).toBe(false);
  });

  it('offers a rebase or merge behind the remote, the lease push too after a push, the lease alone after a rejection', () => {
    expect(jobFollowUps(failedJob('not_fast_forward', { op: 'pull' }))).toEqual([
      'pull_rebase',
      'pull_merge',
    ]);
    expect(jobFollowUps(failedJob('not_fast_forward', { op: 'push' }))).toEqual([
      'pull_rebase',
      'pull_merge',
      'force_push',
    ]);
    expect(jobFollowUps(failedJob('rejected', { op: 'push' }))).toEqual(['force_push']);
    expect(jobFollowUps(failedJob('rejected', { op: 'pull' }))).toEqual([]);
    expect(jobFollowUps(failedJob('conflicts', { op: 'pull' }))).toEqual([]);
    expect(jobFollowUps(doneJob())).toEqual([]);
    expect(jobFollowUps(job())).toEqual([]);
  });

  it('drops the pulls when the status says the upstream is missing or gone, never when it is unavailable', () => {
    const gone = repo({ upstream: { ref: 'origin/main', ahead: null, behind: null } });
    expect(jobFollowUps(failedJob('not_fast_forward', { op: 'push' }), repo())).toEqual(['force_push']);
    expect(jobFollowUps(failedJob('not_fast_forward', { op: 'pull' }), repo())).toEqual([]);
    expect(jobFollowUps(failedJob('rejected', { op: 'push' }), repo())).toEqual(['force_push']);
    expect(jobFollowUps(failedJob('not_fast_forward', { op: 'push' }), gone)).toEqual(['force_push']);
    expect(jobFollowUps(failedJob('not_fast_forward', { op: 'pull' }), gone)).toEqual([]);
    expect(jobFollowUps(failedJob('not_fast_forward', { op: 'push' }), tracking(1, 1))).toEqual([
      'pull_rebase',
      'pull_merge',
      'force_push',
    ]);
    expect(jobFollowUps(failedJob('not_fast_forward', { op: 'pull' }), tracking(0, 1))).toEqual([
      'pull_rebase',
      'pull_merge',
    ]);
    expect(jobFollowUps(failedJob('not_fast_forward', { op: 'push' }), null)).toEqual([
      'pull_rebase',
      'pull_merge',
      'force_push',
    ]);
    expect(jobFollowUps(failedJob('not_fast_forward', { op: 'push' }), undefined)).toEqual([
      'pull_rebase',
      'pull_merge',
      'force_push',
    ]);
  });

  it('labels and confirms each follow-up, naming the upstream and branch when known', () => {
    expect(followUpLabel('pull_rebase')).toBe('Pull with rebase…');
    expect(followUpLabel('pull_merge')).toBe('Pull with merge…');
    expect(followUpLabel('force_push')).toBe('Force push (with lease)…');
    expect(followUpPrompt('pull_rebase', tracking(1, 1))).toEqual({
      title: 'Pull with rebase?',
      body: 'Replays your commits on top of origin/main.',
      confirm: 'Rebase',
    });
    expect(followUpPrompt('pull_merge', tracking(1, 1))).toEqual({
      title: 'Pull with merge?',
      body: 'Merges origin/main into main.',
      confirm: 'Merge',
    });
    expect(followUpPrompt('force_push', tracking(1, 1))).toEqual({
      title: 'Force push?',
      body: "Overwrites origin/main with your branch. Proceeds only if the remote hasn't moved since your last fetch.",
      confirm: 'Force push',
    });
    expect(followUpPrompt('pull_merge', null).body).toBe('Merges the upstream into your branch.');
    expect(followUpPrompt('force_push', undefined).body).toMatch(/^Overwrites the upstream /);
  });

  it('keeps a successful result on screen for six seconds', () => {
    expect(JOB_RESULT_DISMISS_MS).toBe(6_000);
  });

  it('phrases the two job refusals the start and poll routes answer', () => {
    expect(refusalCopy('job_running', 'ab12cd34')).toBe(
      'Another fetch, pull or push is still running.',
    );
    expect(refusalCopy('job_not_found', null)).toBe(
      'Lost track of the job — refresh to see the result.',
    );
  });
});

const gitItem = (over: Partial<GitItem> = {}): GitItem => ({
  kind: 'branch_only',
  branch: 'feature-a',
  worktree_path: null,
  ahead: 0,
  behind: 0,
  detached: false,
  gone: false,
  is_current: false,
  locked: false,
  locked_reason: null,
  prunable: false,
  upstream: null,
  remote: null,
  subject: null,
  committed_at: null,
  ...over,
});

const rebaseInfo = (over: Partial<SessionGitRebaseInfo> = {}): SessionGitRebaseInfo => ({
  branch: 'topic',
  onto_oid: '89abcdef0123456789abcdef0123456789abcdef',
  onto_short_oid: '89abcde',
  onto_ref: 'main',
  stopped_on: { oid: 'fedcba98', short_oid: 'fedcba9', subject: 'Fix the login redirect' },
  step: 2,
  total: 5,
  ...over,
});

const rebasing = (info: SessionGitRebaseInfo | null = rebaseInfo()): SessionGitRepoInfo =>
  repo({
    state: 'rebasing',
    head: { oid: '89abcdef', short_oid: '89abcde', branch: null, detached: true, initial: false },
    rebase: info,
  });

const rebaseResult = (over: Partial<SessionGitRebaseResult> = {}): SessionGitRebaseResult => ({
  outcome: 'rebased',
  rebase: null,
  message: null,
  ...over,
});

describe('rebaseTargets', () => {
  it('lists local branches first, then remote ones labelled as such, each sent as a full ref', () => {
    const items = [
      gitItem({ kind: 'remote_branch', branch: 'origin/main', remote: 'origin' }),
      gitItem({ kind: 'branch_with_worktree', branch: 'topic-b', worktree_path: '/wt/b' }),
      gitItem({ kind: 'branch_only', branch: 'main' }),
      gitItem({ kind: 'remote_branch', branch: 'origin/topic', remote: 'origin' }),
    ];

    expect(rebaseTargets(items, 'topic')).toEqual([
      { value: 'refs/heads/main', label: 'main' },
      { value: 'refs/heads/topic-b', label: 'topic-b' },
      { value: 'refs/remotes/origin/main', label: 'origin/main (remote)' },
      { value: 'refs/remotes/origin/topic', label: 'origin/topic (remote)' },
    ]);
  });

  it("leaves out the session's own branch by name, not the project root's is_current row", () => {
    // A linked-worktree session on `topic`: the root has `main` checked out.
    const items = [
      gitItem({ branch: 'main', is_current: true }),
      gitItem({ kind: 'branch_with_worktree', branch: 'topic', worktree_path: '/wt/topic' }),
    ];

    expect(rebaseTargets(items, 'topic')).toEqual([{ value: 'refs/heads/main', label: 'main' }]);
  });

  it('skips orphan worktrees and rows without a branch, and keeps everything on a detached HEAD', () => {
    const items = [
      gitItem({ kind: 'orphan_worktree', branch: null, worktree_path: '/wt/x', detached: true }),
      gitItem({ kind: 'branch_only', branch: null }),
      gitItem({ kind: 'branch_only', branch: 'main' }),
    ];

    expect(rebaseTargets(items, null)).toEqual([{ value: 'refs/heads/main', label: 'main' }]);
    expect(rebaseTargets([], 'main')).toEqual([]);
  });

  it('sorts the local and the remote group each A→Z, ignoring case', () => {
    const items = [
      gitItem({ kind: 'remote_branch', branch: 'origin/Zed', remote: 'origin' }),
      gitItem({ branch: 'zeta' }),
      gitItem({ kind: 'remote_branch', branch: 'origin/apple', remote: 'origin' }),
      gitItem({ kind: 'branch_with_worktree', branch: 'Beta', worktree_path: '/wt/b' }),
      gitItem({ branch: 'alpha' }),
    ];

    expect(rebaseTargets(items, null).map((target) => target.label)).toEqual([
      'alpha',
      'Beta',
      'zeta',
      'origin/apple (remote)',
      'origin/Zed (remote)',
    ]);
  });
});

describe('shortRefName', () => {
  it('drops a leading refs/heads/ or refs/remotes/, once', () => {
    expect(shortRefName('refs/heads/main')).toBe('main');
    expect(shortRefName('refs/heads/feature/login')).toBe('feature/login');
    expect(shortRefName('refs/remotes/origin/main')).toBe('origin/main');
    expect(shortRefName('refs/heads/refs/remotes/x')).toBe('refs/remotes/x');
  });

  it('leaves a typed ref, a tag or a prefix that is not leading as it is', () => {
    expect(shortRefName('main')).toBe('main');
    expect(shortRefName('origin/main')).toBe('origin/main');
    expect(shortRefName('refs/tags/v1.2.0')).toBe('refs/tags/v1.2.0');
    expect(shortRefName('topic/refs/heads/main')).toBe('topic/refs/heads/main');
  });
});

describe('rebaseBlocker', () => {
  const counts = (over: Partial<NonNullable<SessionGitStatus['counts']>> = {}) => ({
    conflicts: 0,
    staged: 0,
    unstaged: 0,
    untracked: 0,
    total: 0,
    ...over,
  });

  it('lets a clean branch with commits rebase, untracked files and all', () => {
    expect(rebaseBlocker(status())).toBeNull();
    expect(rebaseBlocker(status({ counts: counts({ untracked: 3, total: 3 }) }))).toBeNull();
  });

  it('names what is in the way, the repository first', () => {
    expect(rebaseBlocker(undefined)).toBe('Repository unavailable.');
    expect(rebaseBlocker(status({ repo: null, unavailable_reason: 'not_a_repo' }))).toBe(
      'Repository unavailable.',
    );
    expect(rebaseBlocker(status({ repo: repo({ state: 'merging' }) }))).toBe(
      'A rebase or merge is already in progress.',
    );
    expect(rebaseBlocker(status({ repo: rebasing() }))).toBe(
      'A rebase or merge is already in progress.',
    );
    expect(
      rebaseBlocker(
        status({
          repo: repo({ head: { oid: null, short_oid: null, branch: 'main', detached: false, initial: true } }),
        }),
      ),
    ).toBe('This branch has no commits yet.');
  });

  it.each([
    ['staged', counts({ staged: 1, total: 1 })],
    ['unstaged', counts({ unstaged: 2, total: 2 })],
    ['conflicts', counts({ conflicts: 1, total: 1 })],
  ])('blocks on %s tracked changes', (_label, value) => {
    expect(rebaseBlocker(status({ counts: value }))).toBe('Commit or discard your changes first.');
  });
});

describe('rebaseBanner', () => {
  it('is absent unless the repository is rebasing', () => {
    expect(rebaseBanner(repo(), 0)).toBeNull();
    expect(rebaseBanner(repo({ state: 'merging' }), 1)).toBeNull();
    expect(rebaseBanner(null, 0)).toBeNull();
    expect(rebaseBanner(undefined, 0)).toBeNull();
  });

  it('names the branch, the base, the stop and the step, with a hint for the conflicts left', () => {
    expect(rebaseBanner(rebasing(), 1)).toEqual({
      title: 'Rebasing topic onto main',
      progress: 'Stopped at fedcba9 Fix the login redirect · 2/5',
      hint: '1 conflict — fix the files, then Mark resolved.',
      actionable: true,
    });
    expect(rebaseBanner(rebasing(), 3)?.hint).toBe('3 conflicts — fix the files, then Mark resolved.');
    expect(rebaseBanner(rebasing(), 0)?.hint).toBe('No conflicts left — Continue when ready.');
  });

  it('falls back to the short oid for the base, then drops what the agent could not read', () => {
    expect(rebaseBanner(rebasing(rebaseInfo({ onto_ref: null })), 0)?.title).toBe(
      'Rebasing topic onto 89abcde',
    );
    expect(rebaseBanner(rebasing(rebaseInfo({ onto_ref: null, onto_short_oid: null })), 0)?.title).toBe(
      'Rebasing topic',
    );
    expect(rebaseBanner(rebasing(rebaseInfo({ branch: null })), 0)?.title).toBe('Rebasing onto main');
    expect(rebaseBanner(rebasing(rebaseInfo({ stopped_on: null })), 0)?.progress).toBe('2/5');
    expect(rebaseBanner(rebasing(rebaseInfo({ step: null })), 0)?.progress).toBe(
      'Stopped at fedcba9 Fix the login redirect',
    );
  });

  it('sends a rebase with no details, as a git am session reports it, to the terminal whatever the conflicts', () => {
    const toTerminal = {
      title: 'Rebase in progress',
      progress: null,
      hint: 'Continue, skip or abort it in the terminal.',
      actionable: false,
    };
    expect(rebaseBanner(rebasing(null), 0)).toEqual(toTerminal);
    expect(rebaseBanner(rebasing(null), 2)).toEqual(toTerminal);
  });

  it('keeps the controls for a rebase whose details the agent could not read', () => {
    expect(rebaseBanner(rebasing(rebaseInfo({ branch: null, onto_ref: null, onto_short_oid: null, stopped_on: null, step: null, total: null })), 2)).toEqual({
      title: 'Rebase in progress',
      progress: null,
      hint: '2 conflicts — fix the files, then Mark resolved.',
      actionable: true,
    });
  });
});

describe('rebaseStepPrompt', () => {
  it('names the commit a skip leaves out of the branch being rebased', () => {
    expect(rebaseStepPrompt('skip', rebasing())).toEqual({
      title: 'Skip this commit?',
      body: 'fedcba9 “Fix the login redirect” will be left out of topic.',
      confirm: 'Skip commit',
    });
    expect(rebaseStepPrompt('skip', rebasing(rebaseInfo({ stopped_on: null }))).body).toBe(
      'The commit the rebase stopped at will be left out of topic.',
    );
  });

  it('warns that an abort discards the resolutions made so far', () => {
    expect(rebaseStepPrompt('abort', rebasing())).toEqual({
      title: 'Abort the rebase?',
      body: 'topic goes back to where it was before the rebase. Conflict resolutions made so far are discarded.',
      confirm: 'Abort rebase',
    });
  });

  it("falls back to HEAD's branch, then to a generic name", () => {
    expect(rebaseStepPrompt('abort', repo({ state: 'rebasing' })).body).toMatch(/^main goes back /);
    expect(rebaseStepPrompt('abort', rebasing(null)).body).toMatch(/^the branch goes back /);
    expect(rebaseStepPrompt('skip', undefined).body).toBe(
      'The commit the rebase stopped at will be left out of the branch.',
    );
  });
});

describe('rebaseOutcomeNotice', () => {
  it('reports a finished start with the branch and the picked base, a finished step without it', () => {
    expect(rebaseOutcomeNotice(rebaseResult(), 'topic', 'main')).toEqual({
      tone: 'ok',
      text: 'Rebased topic onto main.',
    });
    expect(rebaseOutcomeNotice(rebaseResult(), 'topic', null)).toEqual({
      tone: 'ok',
      text: 'Rebase finished.',
    });
    expect(rebaseOutcomeNotice(rebaseResult({ outcome: 'up_to_date' }), 'topic', 'main')).toEqual({
      tone: 'ok',
      text: 'topic is already up to date with main.',
    });
    expect(rebaseOutcomeNotice(rebaseResult({ outcome: 'aborted' }), 'topic', null)).toEqual({
      tone: 'ok',
      text: 'Rebase aborted — topic is back where it started.',
    });
  });

  it('names a base picked as a full ref by its short name', () => {
    expect(rebaseOutcomeNotice(rebaseResult(), 'topic', 'refs/heads/main').text).toBe(
      'Rebased topic onto main.',
    );
    expect(
      rebaseOutcomeNotice(rebaseResult({ outcome: 'up_to_date' }), 'topic', 'refs/remotes/origin/main').text,
    ).toBe('topic is already up to date with origin/main.');
  });

  it("reports a stop on the commit's subject, or git's own explanation when it was not a conflict", () => {
    expect(
      rebaseOutcomeNotice(rebaseResult({ outcome: 'stopped', rebase: rebaseInfo() }), 'topic', 'main'),
    ).toEqual({
      tone: 'danger',
      text: 'Rebase stopped at “Fix the login redirect” — resolve the conflicts, then Continue.',
    });
    expect(
      rebaseOutcomeNotice(
        rebaseResult({
          outcome: 'stopped',
          rebase: rebaseInfo(),
          message: 'error: The following untracked working tree files would be overwritten by merge:',
        }),
        'topic',
        'main',
      ),
    ).toEqual({
      tone: 'danger',
      text: 'Rebase stopped: error: The following untracked working tree files would be overwritten by merge:',
    });
    expect(
      rebaseOutcomeNotice(rebaseResult({ outcome: 'stopped', rebase: null, message: '' }), 'topic', null).text,
    ).toBe('Rebase stopped — resolve the conflicts, then Continue.');
  });

  it('does not break on an outcome a newer agent may send', () => {
    expect(rebaseOutcomeNotice(rebaseResult({ outcome: 'future_outcome' }), 'topic', null)).toEqual({
      tone: 'ok',
      text: 'Rebase: future_outcome',
    });
  });
});

describe('rebaseErrorCopy', () => {
  it('reads the shared refusal codes the way a rebase means them', () => {
    expect(rebaseErrorCopy(gitError('unknown_ref', 'nope'))).toBe(
      '“nope” isn\'t a branch, tag or commit here.',
    );
    expect(rebaseErrorCopy(gitError('unknown_ref'))).toBe("That isn't a branch, tag or commit here.");
    expect(rebaseErrorCopy(gitError('no_rebase', 'clean'))).toBe('No rebase is in progress anymore.');
    expect(rebaseErrorCopy(gitError('dirty_worktree', 'keep.txt'))).toBe(
      'Local changes are in the way — stage, commit or discard them first.',
    );
    expect(rebaseErrorCopy(gitError('operation_in_progress', 'merging'))).toBe(
      'A rebase or merge is already in progress.',
    );
    expect(rebaseErrorCopy(gitError('no_head'))).toBe('This branch has no commits yet.');
    expect(rebaseErrorCopy(gitError('hook_failed', 'nope'))).toBe('The pre-rebase hook refused the rebase.');
    expect(rebaseErrorCopy(gitError('git_too_old'))).toBe(
      'Rebasing needs git 2.26 or newer on the agent machine.',
    );
    expect(rebaseErrorCopy(gitError('git_timeout'))).toBe(
      'The rebase took too long and was cut off — continue or abort it.',
    );
  });

  it('falls back to the mutation copy for everything else', () => {
    expect(rebaseErrorCopy(gitError('unmerged', 'unmerged paths in the index'))).toBe(
      'Resolve the conflicts first.',
    );
    expect(rebaseErrorCopy(gitError('job_running', 'ab12'))).toBe(
      'Another fetch, pull or push is still running.',
    );
    expect(rebaseErrorCopy(new TypeError('Failed to fetch'))).toBe(NETWORK_FAILURE_MESSAGE);
  });

  it("shows git's output only for the refusals it explains", () => {
    expect(rebaseErrorOutput(gitError('dirty_worktree', 'unstaged changes to tracked files'))).toBe(
      'unstaged changes to tracked files',
    );
    expect(rebaseErrorOutput(gitError('hook_failed', 'error: The pre-rebase hook refused to rebase.'))).toBe(
      'error: The pre-rebase hook refused to rebase.',
    );
    expect(rebaseErrorOutput(gitError('hook_failed', ''))).toBeNull();
    expect(rebaseErrorOutput(gitError('unknown_ref', 'nope'))).toBeNull();
    expect(rebaseErrorOutput(new TypeError('Failed to fetch'))).toBeNull();
  });
});

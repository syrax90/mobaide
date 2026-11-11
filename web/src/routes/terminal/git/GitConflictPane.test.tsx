import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  SessionGitConflict,
  SessionGitConflictSide,
  SessionGitResolveRequest,
  SessionGitRestoreRequest,
} from '../../../api/sessionGitRepository';
import { recordingClipboard, refusingClipboard } from '../../../test/clipboardStub';

/** One write the pane issued. */
type WriteCall =
  | { kind: 'resolve'; request: SessionGitResolveRequest }
  | { kind: 'take'; path: string; side: SessionGitConflictSide }
  | { kind: 'restore'; request: SessionGitRestoreRequest };

// The repository shadowed, as in `GitChangesPanel.test.tsx`: a read of the
// file answers whatever `api.file` holds at that moment (as `api.status` does
// for the panel's poll), every write is recorded and succeeds unless
// `api.refuse` names a failure for its kind.
const api = vi.hoisted(() => ({
  file: undefined as SessionGitConflict | Error | undefined,
  readCount: 0,
  writes: [] as WriteCall[],
  refuse: undefined as ((kind: WriteCall['kind']) => unknown) | undefined,
}));

vi.mock('../../../api/sessionGitRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/sessionGitRepository')>();
  const write = (call: WriteCall): Promise<void> => {
    api.writes.push(call);
    const refusal = api.refuse?.(call.kind);
    if (refusal === undefined) return Promise.resolve();
    return Promise.reject(refusal instanceof Error ? refusal : new Error('refused'));
  };
  return {
    ...actual,
    getSessionGitConflict: () => {
      api.readCount += 1;
      const next = api.file;
      if (next === undefined) return Promise.reject(new Error('no conflict staged'));
      return next instanceof Error ? Promise.reject(next) : Promise.resolve(next);
    },
    resolveSessionGitConflict: (
      _agentId: string,
      _sessionId: string,
      request: SessionGitResolveRequest,
    ) => write({ kind: 'resolve', request }),
    takeSessionGitConflict: (
      _agentId: string,
      _sessionId: string,
      path: string,
      side: SessionGitConflictSide,
    ) => write({ kind: 'take', path, side }),
    restoreSessionGitConflict: (
      _agentId: string,
      _sessionId: string,
      request: SessionGitRestoreRequest,
    ) => write({ kind: 'restore', request }),
  };
});

const { toApiError } = await import('../../../api/errors');
const { toSessionGitError } = await import('../../../api/sessionGitRepository');
const { GitConflictPane } = await import('./GitConflictPane');

const CONFLICTED = 'a\n<<<<<<< HEAD\nmain\n=======\ntopic\n>>>>>>> fedcba9 (Fix the login redirect)\nz\n';

const conflict = (over: Partial<SessionGitConflict> = {}): SessionGitConflict => ({
  path: 'src/login.py',
  code: 'UU',
  state: 'rebasing',
  kind: 'text',
  content: CONFLICTED,
  token: 't1',
  markers: 1,
  has_base: false,
  ours: { label: 'origin/main', present: true },
  theirs: { label: 'fedcba9 Fix the login redirect', present: true },
  byte_limit: 262144,
  ...over,
});

/** A failure as the repository throws it off the wire. */
const wireFailure = (httpStatus: number, detail: unknown): Error => {
  const error = toSessionGitError(toApiError(new Response(null, { status: httpStatus }), { detail }));
  if (!(error instanceof Error)) throw new Error('expected an Error off the wire');
  return error;
};

let client: QueryClient;
const onBack = vi.fn<() => void>();
const onViewDiff = vi.fn<() => void>();
const onDirtyChange = vi.fn<(dirty: boolean) => void>();
const onDone = vi.fn<(notice: { tone: 'ok' | 'danger'; text: string }) => void>();
const onNotice = vi.fn<(notice: { tone: 'ok' | 'danger'; text: string } | null) => void>();
const onDialogChange = vi.fn<(open: boolean) => void>();

const pane = (over: { conflicted?: boolean; busy?: boolean } = {}) => (
  <QueryClientProvider client={client}>
    <GitConflictPane
      agentId="a1"
      sessionId="s1"
      path="src/login.py"
      conflicted={over.conflicted ?? true}
      busy={over.busy ?? false}
      onBack={onBack}
      onViewDiff={onViewDiff}
      onDirtyChange={onDirtyChange}
      onDone={onDone}
      onNotice={onNotice}
      onDialogChange={onDialogChange}
    />
  </QueryClientProvider>
);

const editor = (): HTMLTextAreaElement =>
  screen.getByRole<HTMLTextAreaElement>('textbox', { name: 'Contents of src/login.py' });
const apply = (): HTMLElement => screen.getByRole('button', { name: 'Apply & mark resolved' });
const saveDraft = (): HTMLElement => screen.getByRole('button', { name: 'Save draft' });
const count = (): HTMLElement => screen.getByRole('status');

/** The textarea is uncontrolled: set its value, then tell React about it. */
const type = (text: string): void => {
  fireEvent.input(editor(), { target: { value: text } });
};

const loaded = async (): Promise<void> => {
  await waitFor(() => {
    expect(screen.getByRole('button', { name: 'More conflict actions' })).toBeInTheDocument();
  });
};

const openPane = async (first: SessionGitConflict = conflict()): Promise<void> => {
  api.file = first;
  render(pane());
  await loaded();
};

const pick = (name: string): void => {
  fireEvent.click(screen.getByRole('button', { name: 'More conflict actions' }));
  fireEvent.click(screen.getByRole('menuitem', { name }));
};

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  api.file = undefined;
  api.readCount = 0;
  api.writes.length = 0;
  api.refuse = undefined;
});

afterEach(() => {
  cleanup();
  client.clear();
  for (const spy of [onBack, onViewDiff, onDirtyChange, onDone, onNotice, onDialogChange]) {
    spy.mockClear();
  }
});

describe('GitConflictPane loading', () => {
  it('opens the file in the editor with its sides named and the heading focused', async () => {
    await openPane();

    expect(editor().value).toBe(CONFLICTED);
    expect(screen.getByRole('heading', { name: 'login.py' })).toHaveFocus();
    expect(screen.getByText('src/login.py')).toBeInTheDocument();
    expect(count()).toHaveTextContent('1 conflict left');
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);
  });

  it('offers a retry when the file cannot be read', async () => {
    api.file = wireFailure(500, { code: 'git_error', message: 'fatal: boom' });
    render(pane());
    const retry = await screen.findByRole('button', { name: 'Retry' });
    api.file = conflict();

    fireEvent.click(retry);

    expect(await screen.findByText('Both sides changed this file.')).toBeInTheDocument();
    expect(editor().value).toBe(CONFLICTED);
  });

  it('sends a file resolved before it could be read back to the list', async () => {
    api.file = wireFailure(409, { code: 'not_conflicted', message: 'src/login.py' });
    render(pane());

    expect(await screen.findByText('login.py is no longer in conflict.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Back to changes' }));

    expect(onBack).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull();
  });

  it.each([
    [
      'an agent without the route',
      'Unknown proxy route',
      'Update your agent',
      'This agent cannot resolve conflicts yet. Update it, or resolve them in the terminal.',
    ],
  ])('offers no retry against %s', async (_name, detail, title, body) => {
    api.file = wireFailure(404, detail);
    render(pane());

    expect(await screen.findByText(title)).toBeInTheDocument();
    expect(screen.getByText(body)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull();
    expect(screen.getByRole('button', { name: '← Changes' })).toBeInTheDocument();
    expect(api.readCount).toBe(1);
  });
});

describe('GitConflictPane editing', () => {
  it('holds Apply while a marker line remains and releases it once the text is clean', async () => {
    await openPane();
    expect(apply()).toBeDisabled();
    expect(apply()).toHaveAttribute('title', 'Remove every conflict marker first');
    expect(saveDraft()).toBeDisabled();

    type('a\nmerged\nz\n');

    expect(onDirtyChange).toHaveBeenLastCalledWith(true);
    expect(saveDraft()).toBeEnabled();
    await waitFor(() => {
      expect(count()).toHaveTextContent('No conflict markers left');
    });
    expect(apply()).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Next conflict' })).toBeDisabled();
  });

  it('still holds Apply for a stray end marker the agent would refuse', async () => {
    await openPane();

    type('a\nmerged\n>>>>>>> fedcba9 (Fix the login redirect)\nz\n');

    await waitFor(() => {
      expect(count()).toHaveTextContent('A conflict marker is left');
    });
    expect(apply()).toBeDisabled();
  });

  it('refuses to send a text over the byte limit until it is cut back down', async () => {
    await openPane(conflict({ byte_limit: 1024 }));

    // 600 units in the textarea, 1 200 bytes on the wire.
    type('é'.repeat(600));

    const notice = await screen.findByRole('alert');
    expect(notice).toHaveTextContent('Too large to save here (over 1 KB).');
    expect(count()).toHaveTextContent('No conflict markers left');
    expect(apply()).toBeDisabled();
    expect(saveDraft()).toBeDisabled();
    fireEvent.keyDown(editor(), { key: 'Enter', ctrlKey: true });
    expect(api.writes).toEqual([]);

    type('é'.repeat(512));

    await waitFor(() => {
      expect(screen.queryByRole('alert')).toBeNull();
    });
    expect(apply()).toBeEnabled();
    expect(saveDraft()).toBeEnabled();
  });

  it('selects the next marker line from the caret, wrapping at the end', async () => {
    const two = 'top\n<<<<<<< a\nx\n=======\ny\n>>>>>>> b\nmid\n<<<<<<< a\nz\n=======\n>>>>>>> b\n';
    await openPane(conflict({ content: two, markers: 2 }));
    const next = screen.getByRole('button', { name: 'Next conflict' });
    const first = two.indexOf('<<<<<<<');
    const second = two.indexOf('<<<<<<<', first + 1);
    editor().setSelectionRange(0, 0);

    fireEvent.click(next);
    expect([editor().selectionStart, editor().selectionEnd]).toEqual([first, first + '<<<<<<< a'.length]);
    expect(editor()).toHaveFocus();

    fireEvent.click(next);
    expect(editor().selectionStart).toBe(second);

    fireEvent.click(next);
    expect(editor().selectionStart).toBe(first);
  });

  it('locks the editor and both saves while something else holds the repository', async () => {
    api.file = conflict({ content: 'clean\n', markers: 0 });
    render(pane({ busy: true }));
    await loaded();

    expect(editor()).toHaveAttribute('readonly');
    expect(apply()).toBeDisabled();
  });
});

describe('GitConflictPane apply and draft', () => {
  it('applies the edited text with the token it was read under and closes', async () => {
    await openPane();
    type('a\nmerged\nz\n');
    await waitFor(() => {
      expect(apply()).toBeEnabled();
    });

    fireEvent.click(apply());

    await waitFor(() => {
      expect(onDone).toHaveBeenCalledWith({ tone: 'ok', text: 'Resolved src/login.py.' });
    });
    expect(api.writes).toEqual([
      {
        kind: 'resolve',
        request: { path: 'src/login.py', token: 't1', content: 'a\nmerged\nz\n', markResolved: true },
      },
    ]);
  });

  it.each([
    ['Ctrl', { ctrlKey: true }],
  ])('applies on %s+Enter from the editor', async (_name, modifier) => {
    await openPane();
    type('merged\n');
    await waitFor(() => {
      expect(apply()).toBeEnabled();
    });

    fireEvent.keyDown(editor(), { key: 'Enter', ...modifier });

    await waitFor(() => {
      expect(api.writes).toHaveLength(1);
    });
    expect(api.writes[0]).toMatchObject({ kind: 'resolve', request: { markResolved: true } });
  });

  it('ignores the shortcut while markers remain, and a bare Enter always', async () => {
    await openPane();

    fireEvent.keyDown(editor(), { key: 'Enter', ctrlKey: true });
    type('merged\n');
    await waitFor(() => {
      expect(apply()).toBeEnabled();
    });
    fireEvent.keyDown(editor(), { key: 'Enter' });

    expect(api.writes).toEqual([]);
  });

  it('saves a draft with its markers, keeps the text and adopts the new token', async () => {
    const draft = 'a\n<<<<<<< HEAD\nhalf\n=======\ntopic\n>>>>>>> x\nz\n';
    await openPane();
    // What the agent reads back is the draft, under a new token.
    api.file = conflict({ content: draft, token: 't2' });
    type(draft);

    fireEvent.click(saveDraft());

    await waitFor(() => {
      expect(onNotice).toHaveBeenLastCalledWith({
        tone: 'ok',
        text: 'Saved src/login.py — the conflict is still open.',
      });
    });
    expect(api.writes).toEqual([
      { kind: 'resolve', request: { path: 'src/login.py', token: 't1', content: draft, markResolved: false } },
    ]);
    expect(onDone).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(api.readCount).toBe(2);
      expect(onDirtyChange).toHaveBeenLastCalledWith(false);
    });
    expect(editor().value).toBe(draft);

    type('a\nmerged\nz\n');
    await waitFor(() => {
      expect(apply()).toBeEnabled();
    });
    // Read in the same tick as the click that sends `t2`: the re-read is in.
    expect(screen.queryByText('Applying will overwrite the other change.')).toBeNull();
    fireEvent.click(apply());

    await waitFor(() => {
      expect(api.writes).toHaveLength(2);
    });
    expect(api.writes[1]).toMatchObject({ request: { token: 't2', markResolved: true } });
  });

  it('warns that Apply will overwrite when the file read back after a draft save is not the draft', async () => {
    const draft = 'a\n<<<<<<< HEAD\nhalf\n=======\ntopic\n>>>>>>> x\nz\n';
    await openPane();
    // Someone wrote the file between the save and the read that follows it.
    api.file = conflict({ content: 'a\nsomeone else\nz\n', token: 't2', markers: 0 });
    type(draft);

    fireEvent.click(saveDraft());

    expect(await screen.findByText('Applying will overwrite the other change.')).toBeInTheDocument();
    expect(editor().value).toBe(draft);
    expect(screen.queryByRole('alert')).toBeNull();
    await waitFor(() => {
      expect(onDirtyChange).toHaveBeenLastCalledWith(false);
    });

    type('a\nmerged\nz\n');
    await waitFor(() => {
      expect(apply()).toBeEnabled();
    });
    fireEvent.click(apply());

    await waitFor(() => {
      expect(api.writes).toHaveLength(2);
    });
    expect(api.writes[1]).toEqual({
      kind: 'resolve',
      request: { path: 'src/login.py', token: 't2', content: 'a\nmerged\nz\n', markResolved: true },
    });
  });

  it('shows any other refusal inline and lets it be dismissed', async () => {
    await openPane();
    api.refuse = () => wireFailure(400, { code: 'too_large' });
    type('merged\n');
    await waitFor(() => {
      expect(apply()).toBeEnabled();
    });

    fireEvent.click(apply());

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('The text is too large to save from here.');
    expect(editor().value).toBe('merged\n');
    expect(onDone).not.toHaveBeenCalled();

    fireEvent.click(within(alert).getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByRole('alert')).toBeNull();
  });
});

describe('GitConflictPane when the file moved on', () => {
  const refusedApply = async (): Promise<HTMLElement> => {
    await openPane();
    api.refuse = () => wireFailure(409, { code: 'file_changed', message: 'src/login.py' });
    type('mine\n');
    await waitFor(() => {
      expect(apply()).toBeEnabled();
    });
    fireEvent.click(apply());
    return screen.findByRole('alert');
  };

  it('keeps the text and offers to reload the file or keep the edits', async () => {
    const strip = await refusedApply();

    expect(strip).toHaveTextContent('login.py changed on the agent machine since you opened it.');
    expect(within(strip).getByRole('button', { name: 'Reload file' })).toBeEnabled();
    expect(within(strip).getByRole('button', { name: 'Keep my edits' })).toBeEnabled();
    expect(editor().value).toBe('mine\n');
    expect(onDone).not.toHaveBeenCalled();
  });

  it('replaces the text with the file as it is now on Reload file', async () => {
    const strip = await refusedApply();
    api.file = conflict({ content: 'theirs now\n', token: 't2', markers: 0 });

    fireEvent.click(within(strip).getByRole('button', { name: 'Reload file' }));

    // The editor text lands at commit; `onDirtyChange` fires from a passive
    // effect that React may flush a task later, so wait for both.
    await waitFor(() => {
      expect(editor().value).toBe('theirs now\n');
      expect(onDirtyChange).toHaveBeenLastCalledWith(false);
    });
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.queryByText('Applying will overwrite the other change.')).toBeNull();
  });

  it('keeps the text on Keep my edits, warns, and applies over the newer file', async () => {
    const strip = await refusedApply();
    api.file = conflict({ content: 'theirs now\n', token: 't2', markers: 0 });
    api.refuse = undefined;

    fireEvent.click(within(strip).getByRole('button', { name: 'Keep my edits' }));

    expect(await screen.findByText('Applying will overwrite the other change.')).toBeInTheDocument();
    expect(editor().value).toBe('mine\n');
    expect(screen.queryByRole('alert')).toBeNull();

    fireEvent.click(apply());

    await waitFor(() => {
      expect(api.writes).toHaveLength(2);
    });
    expect(api.writes[1]).toEqual({
      kind: 'resolve',
      request: { path: 'src/login.py', token: 't2', content: 'mine\n', markResolved: true },
    });
  });
});

describe('GitConflictPane when the conflict is gone', () => {
  const copied = recordingClipboard();

  it('stops the saves and offers the unsaved text for the clipboard once the agent says so', async () => {
    await openPane();
    api.refuse = () => wireFailure(409, { code: 'not_conflicted', message: 'src/login.py' });
    type('mine\n');
    await waitFor(() => {
      expect(apply()).toBeEnabled();
    });

    fireEvent.click(apply());

    const strip = await screen.findByRole('alert');
    expect(strip).toHaveTextContent('login.py is no longer in conflict.');
    expect(apply()).toBeDisabled();
    expect(saveDraft()).toBeDisabled();
    expect(editor()).toHaveAttribute('readonly');

    fireEvent.click(within(strip).getByRole('button', { name: 'Copy my text' }));
    expect(copied).toEqual(['mine\n']);
    await waitFor(() => {
      expect(onNotice).toHaveBeenLastCalledWith({ tone: 'ok', text: 'Copied your text.' });
    });

    fireEvent.click(within(strip).getByRole('button', { name: 'Close' }));
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  describe('on a browser that refuses the clipboard write', () => {
    const attempted = refusingClipboard();

    it('says the text was not copied, since the draft may be the only copy left', async () => {
      await openPane();
      api.refuse = () => wireFailure(409, { code: 'not_conflicted', message: 'src/login.py' });
      type('mine\n');
      await waitFor(() => {
        expect(apply()).toBeEnabled();
      });
      fireEvent.click(apply());
      const strip = await screen.findByRole('alert');

      fireEvent.click(within(strip).getByRole('button', { name: 'Copy my text' }));

      await waitFor(() => {
        expect(onNotice).toHaveBeenLastCalledWith({
          tone: 'danger',
          text: 'Could not copy — select the text instead',
        });
      });
      expect(attempted).toEqual(['mine\n']);
      expect(editor().value).toBe('mine\n');
    });
  });

  it('says so as soon as the status drops the path, with nothing to copy from a clean editor', async () => {
    api.file = conflict();
    const view = render(pane());
    await loaded();

    view.rerender(pane({ conflicted: false }));

    const strip = screen.getByRole('alert');
    expect(strip).toHaveTextContent('login.py is no longer in conflict.');
    expect(within(strip).queryByRole('button', { name: 'Copy my text' })).toBeNull();
    expect(within(strip).getByRole('button', { name: 'Close' })).toBeInTheDocument();
  });
});

describe('GitConflictPane whole-file picks', () => {
  it('asks before taking a side, with Cancel focused, then closes on the answer', async () => {
    await openPane();

    pick('Use fedcba9 Fix the login redirect for the whole file…');

    const confirm = screen.getByRole('alertdialog', {
      name: 'Use fedcba9 Fix the login redirect for the whole file?',
    });
    expect(within(confirm).getByRole('button', { name: 'Cancel' })).toHaveFocus();
    expect(onDialogChange).toHaveBeenLastCalledWith(true);
    expect(api.writes).toEqual([]);

    fireEvent.click(within(confirm).getByRole('button', { name: 'Use this side' }));

    await waitFor(() => {
      expect(onDone).toHaveBeenCalledWith({ tone: 'ok', text: 'Resolved src/login.py.' });
    });
    expect(api.writes).toEqual([{ kind: 'take', path: 'src/login.py', side: 'theirs' }]);
    expect(onDialogChange).toHaveBeenLastCalledWith(false);
  });

  it('offers Keep and Delete, the latter as the destructive one, when a side deleted the file', async () => {
    await openPane(
      conflict({
        code: 'DU',
        content: 'kept\n',
        markers: 0,
        ours: { label: 'origin/main', present: false },
      }),
    );

    pick('Delete the file (origin/main)…');

    const confirm = screen.getByRole('alertdialog', { name: 'Delete login.py?' });
    const destructive = within(confirm).getByRole('button', { name: 'Delete file' });
    expect(destructive).toHaveClass('button-danger');

    fireEvent.click(destructive);

    await waitFor(() => {
      expect(api.writes).toEqual([{ kind: 'take', path: 'src/login.py', side: 'ours' }]);
    });
  });

  it('shows only the picks, as buttons, for a file that cannot be edited', async () => {
    await openPane(conflict({ kind: 'binary', content: null, token: null, markers: 0 }));

    expect(screen.getByText('This is a binary file — pick a side.')).toBeInTheDocument();
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Apply & mark resolved' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Use origin/main for the whole file…' }));
    fireEvent.click(screen.getByRole('button', { name: 'Use this side' }));

    await waitFor(() => {
      expect(api.writes).toEqual([{ kind: 'take', path: 'src/login.py', side: 'ours' }]);
    });
  });

  it('points a submodule or an unknown kind at the terminal with no pick at all', async () => {
    await openPane(conflict({ kind: 'hologram', content: null, token: null, markers: 0 }));

    expect(
      screen.getByText("This conflict can't be resolved here — use the terminal."),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /whole file/ })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'More conflict actions' }));
    expect(screen.getAllByRole('menuitem').map((item) => item.textContent)).toEqual(['View diff']);
  });

  it('hands View diff to the panel, which owns the unsaved-edits question', async () => {
    await openPane();

    pick('View diff');

    expect(onViewDiff).toHaveBeenCalledTimes(1);
  });
});

describe('GitConflictPane start over', () => {
  it.each([
    ['Start over', 'merge'],
  ] as const)('rebuilds the conflict on "%s", guarded by the open file\'s token, and reloads the text', async (label, style) => {
    const rebuilt = 'a\n<<<<<<< origin/main\nmain\n=======\ntopic\n>>>>>>> fedcba9 Fix\nz\n';
    await openPane();
    api.file = conflict({ content: rebuilt, token: 't2' });
    type('half done\n');

    pick('Start over…');
    const confirm = screen.getByRole('alertdialog', { name: 'Start over?' });
    expect(confirm).toHaveTextContent(
      'login.py is rebuilt from both sides. Every edit made to it since the conflict — here or in the terminal — is discarded.',
    );
    expect(within(confirm).getByRole('button', { name: 'Cancel' })).toHaveFocus();
    fireEvent.click(within(confirm).getByRole('button', { name: label }));

    await waitFor(() => {
      expect(editor().value).toBe(rebuilt);
      expect(onDirtyChange).toHaveBeenLastCalledWith(false);
    });
    expect(api.writes).toEqual([
      { kind: 'restore', request: { path: 'src/login.py', style, token: 't1' } },
    ]);
    expect(onDone).not.toHaveBeenCalled();
  });

  it('is not offered when a side has no version to rebuild from', async () => {
    await openPane(
      conflict({
        code: 'UD',
        content: 'kept\n',
        markers: 0,
        theirs: { label: 'fedcba9 Fix the login redirect', present: false },
      }),
    );

    fireEvent.click(screen.getByRole('button', { name: 'More conflict actions' }));

    expect(screen.queryByRole('menuitem', { name: 'Start over…' })).toBeNull();
    expect(screen.getByRole('menuitem', { name: 'View diff' })).toBeInTheDocument();
  });

  it('raises the stale strip instead of reloading when the file moved on first', async () => {
    await openPane();
    api.refuse = () => wireFailure(409, { code: 'file_changed', message: 'src/login.py' });

    pick('Start over…');
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Start over' }));
      await Promise.resolve();
    });

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'login.py changed on the agent machine since you opened it.',
    );
    expect(api.readCount).toBe(1);
    expect(editor().value).toBe(CONFLICTED);
  });
});

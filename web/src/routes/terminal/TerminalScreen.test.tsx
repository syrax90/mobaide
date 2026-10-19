import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
  type RenderResult,
} from '@testing-library/react';
import { StrictMode } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PromptTemplate } from '../../api/templateRepository';
import { recordingClipboard, refusingClipboard } from '../../test/clipboardStub';
import { stubLocalStorage } from '../../test/localStorageStub';
import { stubMediaQuery } from '../../test/matchMediaStub';
import { ApiError } from '../../api/errors';
import type { SessionGitStatus } from '../../api/sessionGitRepository';
import type { SessionResponse } from '../../protocol/dto/session';

// The real model is the point — where the screen mounts the toolbar is a
// property of the tree it builds around it — so only the two things a model
// reaches outside the DOM are replaced. A token that never resolves to a value
// keeps `buildUrl` from producing a URL, so no socket is ever constructed, and
// jsdom ships no `ResizeObserver` for `attach()` to install. Neither touches
// layout: `open()` still no-ops on a zero-sized host, exactly as it does in a
// collapsed container.
vi.mock('../../auth/agentTokens', () => ({
  ensureAgentToken: () => Promise.resolve(null),
  clearAgentToken: () => undefined,
}));

vi.stubGlobal(
  'ResizeObserver',
  class {
    observe = (): void => undefined;
    unobserve = (): void => undefined;
    disconnect = (): void => undefined;
  },
);

// Only the two calls the picker makes over the wire; `groupTemplates` stays
// real, because it decides which section the row the test clicks lands in.
const alpha = vi.hoisted(
  (): PromptTemplate => ({
    templateId: 'id-alpha',
    name: 'alpha',
    body: 'body of alpha',
    isFavorite: false,
    lastUsedAt: null,
  }),
);

// A body the paste sanitizer would visibly rewrite: `sanitizePaste` turns every
// `\n` into `\r` on the way to the PTY, so a clipboard carrying `\r` here holds
// the sanitized form rather than the raw body.
const multiline = vi.hoisted(
  (): PromptTemplate => ({
    templateId: 'id-multi',
    name: 'multi',
    body: 'first line\nsecond line',
    isFavorite: false,
    lastUsedAt: null,
  }),
);

// A body carrying a live `ESC[201~` and a C1 byte — what the clipboard must
// never hand a native terminal verbatim, since the marker would close the
// bracketed-paste span early and leave its tail running as direct input.
const escaped = vi.hoisted(
  (): PromptTemplate => ({
    templateId: 'id-escaped',
    name: 'escaped',
    body: 'review this \u001b[201~echo pwned\u009b0m',
    isFavorite: false,
    lastUsedAt: null,
  }),
);

/** {@link escaped} with ESC and C1 gone; the markers degrade to literal text. */
const escapedStripped = 'review this [201~echo pwned0m';

/** Creates recorded off the wire, plus the failure the editor has to survive. */
const created = vi.hoisted(() => ({
  calls: [] as { name: string; body: string }[],
  error: null as Error | null,
}));

vi.mock('../../api/templateRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/templateRepository')>();
  return {
    ...actual,
    listTemplates: () => Promise.resolve([alpha, multiline, escaped]),
    touchTemplate: () => Promise.resolve(),
    // Shadowed rather than left to the `...actual` spread, which would let the
    // real one reach `relayClient` and fetch.
    createTemplate: (name: string, body: string) => {
      created.calls.push({ name, body });
      return created.error === null
        ? Promise.resolve()
        : Promise.reject(created.error);
    },
  };
});

/** The synced session list; a row's `cwd` is what gates the ⋯ menu. */
const sessions = vi.hoisted(() => ({ rows: [] as SessionResponse[] }));

vi.mock('../../api/sessionRepository', () => ({
  listSessions: () => Promise.resolve(sessions.rows),
}));

// Consulted by the header label once the list is synced; nothing here reads it.
vi.mock('../../api/customAgentTypeRepository', () => ({
  listCustomAgentTypes: () => Promise.resolve([]),
}));

/** One modified file under a clean `main`, enough to see the viewer render. */
const gitStatus = vi.hoisted(
  (): SessionGitStatus => ({
    session_id: 's1',
    cwd: '/srv/p1',
    etag: 'e1',
    unchanged: false,
    unavailable_reason: null,
    repo: {
      toplevel: '/srv/p1',
      git_dir: '/srv/p1/.git',
      common_dir: '/srv/p1/.git',
      head: { oid: 'abc1234def', short_oid: 'abc1234', branch: 'main', detached: false, initial: false },
      upstream: null,
      state: 'clean',
      is_linked_worktree: false,
    },
    entries: [
      {
        group: 'unstaged',
        path: 'src/app.py',
        orig_path: null,
        code: 'M',
        index_status: '.',
        worktree_status: 'M',
        rename_score: null,
        submodule: false,
      },
    ],
    counts: { conflicts: 0, staged: 0, unstaged: 1, untracked: 0, total: 1 },
    truncated: false,
    entry_limit: 5000,
  }),
);

// The network calls shadowed, as above; the error mapping stays real. The
// commit sheet reads its author on open, so that answers too.
vi.mock('../../api/sessionGitRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/sessionGitRepository')>();
  return {
    ...actual,
    getSessionGitStatus: () => Promise.resolve(gitStatus),
    getSessionGitDiff: () => Promise.reject(new Error('diff is not under test')),
    getSessionGitCommitContext: () =>
      Promise.resolve({ author: { name: 'Ada Lovelace', email: 'ada@example.com' }, head: null }),
  };
});

/** The agent's `GET /api/version`, which gates the menu's *Commit…* entry. */
const agentVersion = vi.hoisted((): { capabilities: Record<string, unknown>; probes: number } => ({
  capabilities: {},
  probes: 0,
}));

vi.mock('../../api/agentCapabilities', () => ({
  fetchAgentCapabilities: () => {
    agentVersion.probes += 1;
    return Promise.resolve({
      name: 'mobaide-agent',
      server_version: '1.4.0',
      protocol_versions: [7],
      min_client_version: 1,
      capabilities: agentVersion.capabilities,
    });
  },
}));

// Before any persisted store is imported: the sessions panel's pin lives in
// one, and Node's own `localStorage` throws without `--localstorage-file`.
stubLocalStorage();

const { sessionChannelName } = await import('../../realtime/channels');
const { acquireTerminal, evictTerminalSessions, useTerminalStore } = await import(
  '../../terminal/terminalStore'
);
const { useToastStore } = await import('../../stores/toastStore');
const { TerminalScreen } = await import('./TerminalScreen');
const { useSessionsPanelStore } = await import('../../stores/sessionsPanelStore');
const { WIDE_VIEWPORT_QUERY } = await import('./useWideViewport');
const { sessionResponseSchema } = await import('../../protocol/dto/session');
const { markSessionsSynced, resetSessionsSynced } = await import(
  '../../realtime/__testing__/sessionsSynced'
);

const toastKinds = (): string[] =>
  useToastStore.getState().toasts.map((toast) => toast.kind);

const toastMessage = (): string | undefined =>
  useToastStore.getState().toasts[0]?.message;

function renderTerminal({ strict = false }: { strict?: boolean } = {}): RenderResult {
  const subject = (
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={['/agents/a1/sessions/s1']}>
        <Routes>
          <Route
            path="/agents/:agentId/sessions/:sessionId"
            element={<TerminalScreen />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
  return render(strict ? <StrictMode>{subject}</StrictMode> : subject);
}

afterEach(() => {
  cleanup();
  created.calls.length = 0;
  created.error = null;
  sessions.rows = [];
  agentVersion.capabilities = {};
  agentVersion.probes = 0;
  resetSessionsSynced();
  for (const model of Object.values(useTerminalStore.getState().models)) {
    model.dispose();
  }
  useTerminalStore.setState({ models: {}, order: [] });
  useToastStore.setState({ toasts: [] });
  useSessionsPanelStore.setState({ pinned: false });
});

describe('TerminalScreen scrollbar', () => {
  it('mounts the slider inside the stage and points it at the surface', () => {
    // Inside the stage, not under it: as a flex sibling in a column whose stage
    // is `flex: 1` it would take rows off the grid, and xterm's
    // `SelectionService` clears the selection on any row-count change. The
    // `aria-controls` pairing is the half of it a test can hold, since the two
    // ids are minted in different components.
    const model = acquireTerminal('a1', 's1');
    model.scrollbar.setState({ mode: 'seek', size: 0.2, offset: 0.5 });

    const { container } = renderTerminal();

    const stage = container.querySelector('.terminal-stage');
    const bar = screen.getByRole('scrollbar', { name: 'Terminal scrollback' });
    expect(stage).toContainElement(bar);
    expect(container.querySelector('.terminal-surface')).toHaveAttribute(
      'id',
      bar.getAttribute('aria-controls'),
    );
  });
});

describe('TerminalScreen eviction exemption', () => {
  const key = (): string => sessionChannelName('a1', 's1');
  const model = () => useTerminalStore.getState().models[key()];

  it('holds its model through the eviction that follows the session ending', () => {
    // The screen owns the agent channel now, so the snapshot that removes a
    // session lands while the user is reading it. Without the exemption the
    // model is disposed, the re-acquire effect builds a fresh one, and the
    // scrollback the user was reading is gone.
    const attached = acquireTerminal('a1', 's1');
    renderTerminal();

    evictTerminalSessions('a1', ['s1']);

    expect(model()).toBe(attached);
  });

  it('releases the exemption when the screen goes away', () => {
    // Nothing may stay exempt after unmount: a dead session left resident
    // holds an emulator plus its scrollback for a terminal no one is watching.
    acquireTerminal('a1', 's1');
    const { unmount } = renderTerminal();
    unmount();

    evictTerminalSessions('a1', ['s1']);

    expect(model()).toBeUndefined();
  });
});

describe('TerminalScreen template picks', () => {
  const clipboardWrites = recordingClipboard();

  /**
   * Let the clipboard promise and the toast hanging off it land.
   *
   * The write is issued with no `await` in front of it — the transient user
   * activation Safari and Firefox require survives only a synchronous call — so
   * its continuation is still queued when the click handler returns. One
   * macrotask is past every microtask hop `writeClipboardText` takes.
   */
  const flush = async (): Promise<void> => {
    await act(async () => {
      await new Promise<void>((resolve) => setTimeout(resolve, 0));
    });
  };

  /**
   * The model calls a pick makes, spied for the reasons the first test gives.
   * Hands back the `pasteText` spy, which is the only one a caller inspects.
   */
  const stubModelCalls = (model: ReturnType<typeof acquireTerminal>, pasted: boolean) => {
    const paste = vi.spyOn(model, 'pasteText').mockImplementation(() => pasted);
    vi.spyOn(model, 'jumpToBottom').mockImplementation(() => undefined);
    vi.spyOn(model, 'focus').mockImplementation(() => undefined);
    return paste;
  };

  /**
   * Open the picker over an opened send gate and hand back one row, unclicked —
   * the caller clicks, because what happens in that same synchronous task is
   * half of what these tests are about. Seeding order as in the first test.
   */
  const openPicker = async (
    model: ReturnType<typeof acquireTerminal>,
    body: RegExp,
  ): Promise<HTMLElement> => {
    fireEvent.click(screen.getByRole('button', { name: 'Templates' }));
    const row = await screen.findByRole('button', { name: body });
    act(() => {
      model.state.setState({
        connection: 'live',
        status: { status: 'running', pid: undefined, exit_code: undefined },
      });
    });
    return row;
  };

  it('pastes the picked body into the terminal and hands focus back to the grid', async () => {
    // Picking must not execute anything: the body is pasted onto the program's
    // input line for review. The model calls are spied rather than observed
    // through xterm because the harness never attaches the terminal to a live
    // socket — the real `pasteText` would refuse and `focus()` would reach
    // into an element jsdom never laid out. The seeded state opens the view's
    // `canSend` gate so the pick is allowed through.
    const model = acquireTerminal('a1', 's1');
    const paste = vi.spyOn(model, 'pasteText').mockImplementation(() => true);
    const jump = vi.spyOn(model, 'jumpToBottom').mockImplementation(() => undefined);
    const focus = vi.spyOn(model, 'focus').mockImplementation(() => undefined);
    renderTerminal();

    fireEvent.click(screen.getByRole('button', { name: 'Templates' }));
    const pickButton = await screen.findByRole('button', { name: /body of alpha/ });
    // Seeded only now, not before mount: the mocked token resolves to null,
    // so the model's own channel asynchronously reports `disconnected` while
    // the picker's `listTemplates()` query is still in flight, clobbering a
    // seed applied any earlier. `act` flushes the resulting re-render
    // synchronously so the click below reads the opened gate, not a stale one.
    act(() => {
      model.state.setState({
        connection: 'live',
        status: { status: 'running', pid: undefined, exit_code: undefined },
      });
    });
    fireEvent.click(pickButton);

    expect(paste).toHaveBeenCalledWith(alpha.body);
    // The paste bypasses xterm's user-input scroll, so the screen jumps to the
    // tail where the body landed.
    expect(jump).toHaveBeenCalled();
    expect(focus).toHaveBeenCalled();
    // The picker closes with the pick; focus must not be stranded on a button
    // that just left the DOM.
    expect(screen.queryByRole('dialog', { name: 'Templates' })).toBeNull();
  });

  it('keeps the picker open and explains when the session is not accepting input', async () => {
    // A closed send gate must not silently discard the body: the old composer
    // flow held it in local state, so the paste flow owes the user the same
    // no-loss guarantee — the picker stays open for a retry. The refusal is no
    // longer a toast (the picker sits on the top layer, above `.toast-viewport`)
    // but a persistent inline banner, and the pick rows themselves are disabled
    // so a click cannot reach `onPick` at all.
    const model = acquireTerminal('a1', 's1');
    const paste = vi.spyOn(model, 'pasteText');
    renderTerminal();

    fireEvent.click(screen.getByRole('button', { name: 'Templates' }));
    const pickButton = await screen.findByRole('button', { name: /body of alpha/ });
    expect(pickButton).toBeDisabled();
    fireEvent.click(pickButton);

    expect(paste).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog', { name: 'Templates' })).toBeInTheDocument();
    expect(screen.getByText(/session is not accepting input/)).toBeInTheDocument();
    expect(useToastStore.getState().toasts).toEqual([]);
    // Nothing reached the input line, so there is nothing a second paste could
    // expand — the user's clipboard must survive the refused pick intact.
    expect(clipboardWrites).toEqual([]);
  });

  it('hands focus back to the grid when the picker is dismissed without a pick', async () => {
    // Closing is a dismissal path like any other, and the grid has to end up
    // with the caret — otherwise the next keystroke goes nowhere. It cannot be
    // done from the dismiss callback: `Modal` closes its `<dialog>` in a
    // layout-effect cleanup that runs afterwards, and the native close
    // algorithm hands focus to the button that opened it.
    const model = acquireTerminal('a1', 's1');
    const focus = vi.spyOn(model, 'focus').mockImplementation(() => undefined);
    renderTerminal();

    fireEvent.click(screen.getByRole('button', { name: 'Templates' }));
    const picker = await screen.findByRole('dialog', { name: 'Templates' });
    // The surface focuses the grid on attach, so only the calls after this
    // point belong to the dismissal.
    const beforeClose = focus.mock.calls.length;
    fireEvent.click(within(picker).getByRole('button', { name: 'Close' }));

    expect(screen.queryByRole('dialog', { name: 'Templates' })).toBeNull();
    expect(focus.mock.calls.length).toBe(beforeClose + 1);
  });

  it("keeps the body's line breaks on the clipboard, within the click", async () => {
    // Claude Code collapses a multi-line paste to a `[Pasted text #n]`
    // placeholder and tells the user to paste again to expand it, which only
    // works if the body is on the system clipboard. It goes on with its line
    // breaks intact rather than in the `\r`-joined form the PTY got: both
    // pastes run the same encoder, which is idempotent on `\r` and absorbs the
    // `\n` → `\r\n` a Windows clipboard round trip adds, so either form
    // converges on the same bytes inside the CLI — but the `\r`-joined one
    // renders as a single run-on line in every consumer that is not a
    // terminal, which is where the user actually looks at what they copied.
    const model = acquireTerminal('a1', 's1');
    stubModelCalls(model, true);
    renderTerminal();

    const row = await openPicker(model, /second line/);
    fireEvent.click(row);

    // Asserted before the flush: an `await` anywhere ahead of the write would
    // spend the click's transient user activation, which is the whole of
    // Safari's and Firefox's permission for a programmatic clipboard write.
    expect(clipboardWrites).toEqual([multiline.body]);
    await flush();

    expect(toastKinds()).toEqual(['success']);
    // Pinned whole, because the wording is the claim: the client cannot
    // confirm the body reached the PTY — a half-open socket still reads
    // `readyState === OPEN` — and the Templates button is unconditional, so
    // this screen never learns whether the session runs Claude Code or a plain
    // shell. Only the copy and the collapse hint may be spoken aloud.
    expect(toastMessage()).toBe('Template copied — paste again if it was collapsed.');
  });

  it('copies the body with ESC and C1 stripped, and pastes it unchanged', async () => {
    // The clipboard is the one consumer the escape-injection defense cannot
    // reach. `pasteText` strips ESC and C1 on the way to the PTY, but paste the
    // copied body into a *native* terminal and a live `ESC[201~` in it closes
    // the bracketed-paste span early, leaving the tail to run as direct input
    // (the CVE-2021-31701 class `terminalPaste` documents). The wire is
    // unaffected: `stripControls` is idempotent and the newline pass needs an
    // `\n`, so `sanitizePaste(stripControls(b))` is `sanitizePaste(b)` and the
    // model still receives the body exactly as stored.
    const model = acquireTerminal('a1', 's1');
    const paste = stubModelCalls(model, true);
    renderTerminal();

    const row = await openPicker(model, /review this/);
    fireEvent.click(row);

    expect(clipboardWrites).toEqual([escapedStripped]);
    expect(paste).toHaveBeenCalledWith(escaped.body);
  });

  it('leaves the clipboard alone when the paste itself is refused', async () => {
    // `pasteText` also refuses a body that is empty after control-stripping or
    // over the 1 MiB frame cap, with the gate wide open. Nothing landed on the
    // input line, so there is nothing a second paste could expand: clobbering
    // the clipboard would cost the user what was on it and buy nothing, and a
    // toast would announce a trade that should never have happened.
    const model = acquireTerminal('a1', 's1');
    stubModelCalls(model, false);
    renderTerminal();

    const row = await openPicker(model, /body of alpha/);
    fireEvent.click(row);
    await flush();

    expect(clipboardWrites).toEqual([]);
    expect(toastKinds()).toEqual([]);
  });

  it('drops the toast when the screen goes away while the write is in flight', async () => {
    // A clipboard permission prompt can hold the write open long enough for the
    // user to leave, and the continuation would then toast over whatever screen
    // replaced this one. The unmount is synchronous with the click, so the
    // promise is still pending when the component dies.
    const model = acquireTerminal('a1', 's1');
    stubModelCalls(model, true);
    const { unmount } = renderTerminal();

    const row = await openPicker(model, /body of alpha/);
    fireEvent.click(row);
    unmount();
    await flush();

    expect(clipboardWrites).toEqual([alpha.body]);
    expect(toastKinds()).toEqual([]);
  });

  describe('with the clipboard write refused', () => {
    const refusedWrites = refusingClipboard();

    it('names the copy as what failed, and claims nothing else', async () => {
      // Firefox and Safari refuse the write outright when they judge the user
      // gesture spent. Only the copy failed, so the toast reports exactly that
      // — it does not report the insert either way, which is the half the
      // client cannot verify from here. The message leads with "the template"
      // so it reads as a failed copy rather than a failed pick, and so it no
      // longer collides with `sessionModel.copySelection`'s identical refusal
      // toast — a failed template copy and a failed selection copy are now
      // distinguishable in one viewport.
      const model = acquireTerminal('a1', 's1');
      stubModelCalls(model, true);
      renderTerminal();

      const row = await openPicker(model, /body of alpha/);
      fireEvent.click(row);
      await flush();

      expect(refusedWrites).toEqual([alpha.body]);
      expect(toastKinds()).toEqual(['error']);
      expect(toastMessage()).toBe(
        'Could not copy the template — the browser refused the clipboard write.',
      );
    });
  });

  // `main.tsx` wraps the whole tree in `<StrictMode>`, so this — not the bare
  // render every test above uses — is the shape every real user gets. StrictMode
  // runs each effect setup → cleanup → setup on mount, and the ref that decides
  // whether the write's continuation may still toast is latched off by that
  // simulated unmount unless the setup half restores it: refs survive the
  // remount, so a cleanup-only ref would silently swallow every toast in
  // production while passing the bare-render tests.
  describe('under StrictMode, as the app mounts it', () => {
    it('still toasts once the clipboard write lands', async () => {
      const model = acquireTerminal('a1', 's1');
      stubModelCalls(model, true);
      renderTerminal({ strict: true });

      const row = await openPicker(model, /body of alpha/);
      fireEvent.click(row);
      await flush();

      expect(clipboardWrites).toEqual([alpha.body]);
      expect(toastKinds()).toEqual(['success']);
    });
  });
});

describe('TerminalScreen template creation', () => {
  /** Walk the header button into the picker, then into the editor. */
  const openEditor = async (): Promise<void> => {
    fireEvent.click(screen.getByRole('button', { name: 'Templates' }));
    await screen.findByRole('button', { name: 'New template' });
    fireEvent.click(screen.getByRole('button', { name: 'New template' }));
    await screen.findByRole('dialog', { name: 'New template' });
  };

  const nameField = (): HTMLElement => screen.getByLabelText('Name');
  const bodyField = (): HTMLElement => screen.getByLabelText('Prompt body');

  /** React Query settles its mutation on a timer, outside the click's task. */
  const flush = async (): Promise<void> => {
    await act(async () => {
      await new Promise<void>((resolve) => setTimeout(resolve, 0));
    });
  };

  it('opens the editor on the terminal selection, with the picker gone', async () => {
    // The single-dialog invariant. `TemplatesPicker` hangs its key handler on
    // the ancestor of everything it renders, and the top layer reorders paint
    // rather than propagation — so an editor left mounted inside it would lose
    // its textarea's arrow keys to the row walk and its draft to the picker's
    // Escape. The panel union is what makes that unrepresentable.
    const model = acquireTerminal('a1', 's1');
    vi.spyOn(model, 'selectionText').mockReturnValue('npm run build\nfailed');
    renderTerminal();

    await openEditor();

    expect(screen.queryByRole('dialog', { name: 'Templates' })).toBeNull();
    expect(bodyField()).toHaveValue('npm run build\nfailed');
    expect(nameField()).toHaveValue('');
  });

  it('opens empty when nothing is selected', async () => {
    const model = acquireTerminal('a1', 's1');
    vi.spyOn(model, 'selectionText').mockReturnValue('');
    renderTerminal();

    await openEditor();

    expect(bodyField()).toHaveValue('');
    // Both fields are required, so an empty body cannot be saved by naming it.
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  it('snapshots the selection when the picker opens, not when the editor does', async () => {
    // The reason the selection is carried in the panel rather than read late:
    // PTY output keeps arriving behind the modal, and a mouse-protocol change,
    // an alternate-screen switch, a row-count change and the replay wipe each
    // clear xterm's selection. Reading at editor-open would hand back whatever
    // survived that; reading at picker-open cannot.
    const model = acquireTerminal('a1', 's1');
    const selection = vi.spyOn(model, 'selectionText').mockReturnValue('first');
    renderTerminal();

    fireEvent.click(screen.getByRole('button', { name: 'Templates' }));
    await screen.findByRole('button', { name: 'New template' });
    selection.mockReturnValue('second');
    fireEvent.click(screen.getByRole('button', { name: 'New template' }));
    await screen.findByRole('dialog', { name: 'New template' });

    expect(bodyField()).toHaveValue('first');
    expect(selection).toHaveBeenCalledTimes(1);
  });

  it('trims the name, sends the body verbatim, and closes to the terminal', async () => {
    // Trailing whitespace in a body is load-bearing — it reaches the PTY
    // unchanged — so only the name is trimmed. The toast is raised after the
    // dialog is gone, because `.toast-viewport` paints below the top layer.
    const model = acquireTerminal('a1', 's1');
    vi.spyOn(model, 'selectionText').mockReturnValue('git status  ');
    const focus = vi.spyOn(model, 'focus').mockImplementation(() => undefined);
    renderTerminal();
    await openEditor();

    fireEvent.change(nameField(), { target: { value: '  Standup  ' } });
    const before = focus.mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await flush();

    expect(created.calls).toEqual([{ name: 'Standup', body: 'git status  ' }]);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(toastKinds()).toEqual(['success']);
    expect(toastMessage()).toBe('Saved template "Standup"');
    // The grid takes focus back on the dismissal, not on the picker-to-editor
    // hop that preceded it.
    expect(focus.mock.calls.length).toBeGreaterThan(before);
  });

  it('keeps the editor and the draft when the save fails', async () => {
    // The draft is the expensive thing to lose, and the failure has to render
    // inline: it is raised while the dialog is still up, which is precisely
    // where a toast cannot be seen.
    created.error = new ApiError({
      status: 500,
      message: 'relay refused',
      code: null,
      retryAfterSeconds: null,
      detail: null,
    });
    const model = acquireTerminal('a1', 's1');
    vi.spyOn(model, 'selectionText').mockReturnValue('git status');
    renderTerminal();
    await openEditor();

    fireEvent.change(nameField(), { target: { value: 'Standup' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await flush();

    expect(screen.getByText('Failed to save template: relay refused')).toBeInTheDocument();
    expect(screen.getByRole('dialog', { name: 'New template' })).toBeInTheDocument();
    expect(bodyField()).toHaveValue('git status');
    expect(toastKinds()).toEqual([]);
  });

  it('returns to the terminal, not the picker, when cancelled', async () => {
    // Save has to close everything for its toast to be visible, so Cancel lands
    // in the same place: two exits to different screens would be the
    // surprising half of the pair.
    const model = acquireTerminal('a1', 's1');
    vi.spyOn(model, 'selectionText').mockReturnValue('git status');
    const focus = vi.spyOn(model, 'focus').mockImplementation(() => undefined);
    renderTerminal();
    await openEditor();

    const before = focus.mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await flush();

    expect(screen.queryByRole('dialog')).toBeNull();
    expect(created.calls).toEqual([]);
    expect(focus.mock.calls.length).toBeGreaterThan(before);
  });
});

describe('TerminalScreen git changes', () => {
  const row = (over: Record<string, unknown> = {}): SessionResponse =>
    sessionResponseSchema.parse({
      session_id: 's1',
      state: 'running',
      created_at: '2026-08-04T10:00:00Z',
      ...over,
    });

  const more = (): HTMLElement | null =>
    screen.queryByRole('button', { name: 'More options' });

  /** The synced list and the terminal; the kind label proves the row landed. */
  const renderSynced = async (...rows: SessionResponse[]): Promise<void> => {
    markSessionsSynced('a1');
    sessions.rows = rows;
    acquireTerminal('a1', 's1');
    renderTerminal();
    await screen.findByText('Claude Code');
  };

  /** Walk the ⋯ menu into the viewer. */
  const openChanges = async (): Promise<{ trigger: HTMLElement; panel: HTMLElement }> => {
    const trigger = await screen.findByRole('button', { name: 'More options' });
    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole('menuitem', { name: 'Changes…' }));
    const panel = await screen.findByRole('dialog', { name: 'Git changes' });
    return { trigger, panel };
  };

  it('offers no git menu for a session whose row names no folder', async () => {
    // Both mobile clients gate on `cwd`: the git routes answer for the
    // directory the session was spawned in, and a row without one has nothing
    // to show. The label is the proof that the row itself did arrive.
    await renderSynced(row());

    expect(more()).toBeNull();
  });

  it('opens the Changes viewer from the ⋯ menu once the row names a folder', async () => {
    await renderSynced(row({ cwd: '/srv/p1', project_name: 'p1' }));

    const { panel } = await openChanges();

    // The menu is gone with the pick, and the viewer is non-modal: the
    // terminal underneath stays live, which is the point of a viewer next to
    // the PTY producing the changes.
    expect(screen.queryByRole('menuitem')).toBeNull();
    expect(panel).toHaveAttribute('aria-modal', 'false');
    expect(await within(panel).findByText('app.py')).toBeInTheDocument();
    expect(within(panel).getByText('main')).toBeInTheDocument();
  });

  it('closes the viewer on Escape and hands focus back to the ⋯ button', async () => {
    // Back to the menu button, not the grid: the viewer was reached from the
    // header, and a keyboard user is still up there.
    await renderSynced(row({ cwd: '/srv/p1' }));
    const { trigger, panel } = await openChanges();
    await within(panel).findByText('app.py');

    fireEvent.keyDown(panel, { key: 'Escape' });

    expect(screen.queryByRole('dialog', { name: 'Git changes' })).toBeNull();
    expect(trigger).toHaveFocus();
  });

  /** React Query notifies on a timer, so nothing it does lands in this task. */
  const flush = async (): Promise<void> => {
    await act(async () => {
      await new Promise<void>((resolve) => setTimeout(resolve, 0));
    });
  };

  it('offers Commit… only once the agent advertises capabilities.git.commit', async () => {
    // Reads the probe once per screen: the entry exists on an explicit true,
    // so an older agent keeps the menu it had.
    agentVersion.capabilities = { git: { changes: true } };
    await renderSynced(row({ cwd: '/srv/p1' }));
    await waitFor(() => {
      expect(agentVersion.probes).toBe(1);
    });
    await flush();

    fireEvent.click(await screen.findByRole('button', { name: 'More options' }));

    expect(screen.getByRole('menuitem', { name: 'Changes…' })).toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: 'Commit…' })).toBeNull();
  });

  it('asks the agent nothing while the row names no folder', async () => {
    // The probe is gated like the menu it feeds: without a cwd there are no
    // git routes to offer, so the agent is not asked what it could do.
    agentVersion.capabilities = { git: { commit: true } };
    await renderSynced(row());
    await flush();

    expect(agentVersion.probes).toBe(0);
    expect(more()).toBeNull();
  });

  it('opens the viewer with the commit sheet already up from Commit…', async () => {
    agentVersion.capabilities = { git: { changes: true, stage: true, discard: true, commit: true } };
    await renderSynced(row({ cwd: '/srv/p1' }));

    fireEvent.click(await screen.findByRole('button', { name: 'More options' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Commit…' }));

    const panel = await screen.findByRole('dialog', { name: 'Git changes' });
    const sheet = await screen.findByRole('dialog', { name: 'Commit' });
    expect(
      await within(sheet).findByText('Committing as Ada Lovelace <ada@example.com>'),
    ).toBeInTheDocument();
    expect(await within(panel).findByText('app.py')).toBeInTheDocument();
    expect(screen.queryByRole('menuitem')).toBeNull();
  });

  it('offers Fetch / Pull / Push… on any job capability and opens the viewer with the sheet up', async () => {
    agentVersion.capabilities = { git: { changes: true, push: true } };
    await renderSynced(row({ cwd: '/srv/p1' }));

    fireEvent.click(await screen.findByRole('button', { name: 'More options' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Fetch / Pull / Push…' }));

    const panel = await screen.findByRole('dialog', { name: 'Git changes' });
    const sheet = await screen.findByRole('dialog', { name: 'Fetch / Pull / Push' });
    // Only push is advertised and the row has no upstream yet: one row, Publish branch.
    expect(
      [...sheet.querySelectorAll('.git-sync-row-label')].map((label) => label.textContent),
    ).toEqual(['Publish branch']);
    expect(await within(panel).findByText('app.py')).toBeInTheDocument();
    expect(screen.queryByRole('menuitem')).toBeNull();
  });

  it('keeps Fetch / Pull / Push… off the menu of an agent that only stages and commits', async () => {
    agentVersion.capabilities = { git: { changes: true, stage: true, discard: true, commit: true } };
    await renderSynced(row({ cwd: '/srv/p1' }));

    fireEvent.click(await screen.findByRole('button', { name: 'More options' }));

    expect(screen.getByRole('menuitem', { name: 'Commit…' })).toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: 'Fetch / Pull / Push…' })).toBeNull();
  });

  it('offers Rebase… after Branches… only once the agent advertises capabilities.git.rebase', async () => {
    agentVersion.capabilities = {
      git: { changes: true, stage: true, discard: true, commit: true, branches: true, rebase: true },
    };
    await renderSynced(row({ cwd: '/srv/p1', project_name: 'p1' }));
    await waitFor(() => {
      expect(agentVersion.probes).toBe(1);
    });
    await flush();

    fireEvent.click(await screen.findByRole('button', { name: 'More options' }));

    const labels = screen.getAllByRole('menuitem').map((item) => item.textContent);
    expect(labels.slice(-2)).toEqual(['Branches…', 'Rebase…']);
  });

  it('keeps Rebase… off the menu of an agent that does not advertise it', async () => {
    agentVersion.capabilities = {
      git: { changes: true, stage: true, discard: true, commit: true, branches: true, resolve: true },
    };
    await renderSynced(row({ cwd: '/srv/p1', project_name: 'p1' }));
    await waitFor(() => {
      expect(agentVersion.probes).toBe(1);
    });
    await flush();

    fireEvent.click(await screen.findByRole('button', { name: 'More options' }));

    expect(screen.getByRole('menuitem', { name: 'Branches…' })).toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: 'Rebase…' })).toBeNull();
  });

  it('opens the viewer with the rebase sheet already up from Rebase…', async () => {
    agentVersion.capabilities = { git: { changes: true, stage: true, rebase: true } };
    await renderSynced(row({ cwd: '/srv/p1' }));

    fireEvent.click(await screen.findByRole('button', { name: 'More options' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Rebase…' }));

    const panel = await screen.findByRole('dialog', { name: 'Git changes' });
    const sheet = await screen.findByRole('dialog', { name: 'Rebase main' });
    // No project on the row: the base is typed rather than picked.
    expect(within(sheet).getByPlaceholderText('Branch, tag or commit')).toBeInTheDocument();
    expect(await within(panel).findByText('app.py')).toBeInTheDocument();
    expect(screen.queryByRole('menuitem')).toBeNull();
  });
});

describe('TerminalScreen sessions panel', () => {
  const toggle = (): HTMLElement => screen.getByRole('button', { name: 'Sessions' });
  const overlay = (): HTMLElement | null =>
    screen.queryByRole('complementary', { name: 'Sessions panel' });
  const docked = (container: HTMLElement): Element | null =>
    container.querySelector('.sessions-panel-docked');

  /**
   * Answer the wide-viewport query; `setup.ts`'s `matchMedia` is a constant
   * `false`. Re-stubbed narrow afterwards rather than `vi.unstubAllGlobals`,
   * which would take the localStorage stand-in with it.
   */
  const stubViewport = (wide: boolean): void => {
    stubMediaQuery(WIDE_VIEWPORT_QUERY, wide);
  };

  afterEach(() => {
    stubViewport(false);
  });

  it('opens the panel as a popover from the Sessions button and hands focus back on close', () => {
    acquireTerminal('a1', 's1');
    renderTerminal();
    expect(overlay()).toBeNull();
    expect(toggle()).toHaveAttribute('aria-expanded', 'false');

    fireEvent.click(toggle());

    const panel = overlay();
    expect(panel).not.toBeNull();
    // `auto`, not `manual`: a click into the grid or Escape dismisses it, and
    // the UA backdrop is the scrim — the terminal underneath stays live.
    expect(panel).toHaveAttribute('popover', 'auto');
    expect(panel?.dataset['popoverOpen']).toBe('1');
    expect(toggle()).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('heading', { name: 'Sessions' })).toHaveFocus();

    fireEvent.click(screen.getByRole('button', { name: 'Close sessions panel' }));

    expect(overlay()).toBeNull();
    expect(toggle()).toHaveAttribute('aria-expanded', 'false');
    expect(toggle()).toHaveFocus();
  });

  it('follows a light dismiss so the next click opens it again', () => {
    // Escape and a click outside close the popover without going through
    // React; the `toggle` event is the only word of it.
    acquireTerminal('a1', 's1');
    renderTerminal();
    fireEvent.click(toggle());
    const panel = overlay();
    expect(panel).not.toBeNull();

    act(() => {
      panel?.hidePopover();
    });

    expect(overlay()).toBeNull();
    expect(toggle()).toHaveAttribute('aria-expanded', 'false');

    fireEvent.click(toggle());
    expect(overlay()).not.toBeNull();
  });

  it('docks the panel beside the stage when pinned on a wide viewport', () => {
    // Beside `.terminal-page`, never inside it: that one is a column and a
    // child there would stack under the grid.
    stubViewport(true);
    useSessionsPanelStore.setState({ pinned: true });
    acquireTerminal('a1', 's1');
    const { container } = renderTerminal();

    const rail = docked(container);
    expect(rail).not.toBeNull();
    expect(rail?.nextElementSibling).toBe(container.querySelector('.terminal-page'));
    expect(overlay()).toBeNull();
    expect(toggle()).toHaveAttribute('aria-expanded', 'true');
    expect(screen.queryByRole('button', { name: 'Close sessions panel' })).toBeNull();
    expect(screen.getByRole('heading', { name: 'Sessions' })).not.toHaveFocus();

    // The toggle's job while docked is to unpin: the rail leaves and the pin
    // is forgotten for the next visit too.
    fireEvent.click(toggle());

    expect(docked(container)).toBeNull();
    expect(useSessionsPanelStore.getState().pinned).toBe(false);
    expect(toggle()).toHaveAttribute('aria-expanded', 'false');
  });

  it('keeps the overlay form when pinned on a narrow viewport', () => {
    // The pin waits for the window to grow again; a 300 px rail on a phone
    // would leave nothing for the grid.
    stubViewport(false);
    useSessionsPanelStore.setState({ pinned: true });
    acquireTerminal('a1', 's1');
    const { container } = renderTerminal();

    expect(docked(container)).toBeNull();
    expect(overlay()).toBeNull();

    fireEvent.click(toggle());

    expect(overlay()).not.toBeNull();
    expect(docked(container)).toBeNull();
    expect(useSessionsPanelStore.getState().pinned).toBe(true);
  });

  it('turns the rail into an open overlay when unpinned from the panel on a wide viewport', () => {
    // Unpinning from inside the docked panel must not make it vanish under
    // the click; it stays on screen as the overlay.
    stubViewport(true);
    useSessionsPanelStore.setState({ pinned: true });
    acquireTerminal('a1', 's1');
    const { container } = renderTerminal();
    const rail = docked(container);
    expect(rail).not.toBeNull();

    fireEvent.click(within(rail as HTMLElement).getByRole('button', { name: 'Unpin' }));

    expect(docked(container)).toBeNull();
    expect(overlay()).not.toBeNull();
    expect(useSessionsPanelStore.getState().pinned).toBe(false);
  });

  it('docks the overlay when pinned from it on a wide viewport', () => {
    stubViewport(true);
    acquireTerminal('a1', 's1');
    const { container } = renderTerminal();
    fireEvent.click(toggle());
    const panel = overlay();
    expect(panel).not.toBeNull();

    fireEvent.click(within(panel as HTMLElement).getByRole('button', { name: 'Pin' }));

    expect(overlay()).toBeNull();
    expect(docked(container)).not.toBeNull();
    expect(useSessionsPanelStore.getState().pinned).toBe(true);
  });

  it('leaves focus in the rail when pinned from the overlay on a wide viewport', () => {
    // The overlay closes its own popover on unmount, and that raises the same
    // `toggle` event a light dismiss does. Mistaken for one, it would hand
    // focus to the Sessions button while the panel is still on screen as the
    // rail.
    stubViewport(true);
    acquireTerminal('a1', 's1');
    const { container } = renderTerminal();
    fireEvent.click(toggle());
    const panel = overlay();
    expect(panel).not.toBeNull();
    const pin = within(panel as HTMLElement).getByRole('button', { name: 'Pin' });
    act(() => {
      pin.focus();
    });

    fireEvent.click(pin);

    expect(docked(container)).not.toBeNull();
    expect(toggle()).toHaveAttribute('aria-expanded', 'true');
    expect(toggle()).not.toHaveFocus();
  });
});

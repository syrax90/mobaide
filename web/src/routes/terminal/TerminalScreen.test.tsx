import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
  type RenderResult,
} from '@testing-library/react';
import { StrictMode } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PromptTemplate } from '../../api/templateRepository';
import { recordingClipboard, refusingClipboard } from '../../test/clipboardStub';

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

vi.mock('../../api/templateRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/templateRepository')>();
  return {
    ...actual,
    listTemplates: () => Promise.resolve([alpha, multiline, escaped]),
    touchTemplate: () => Promise.resolve(),
  };
});

const { sessionChannelName } = await import('../../realtime/channels');
const { acquireTerminal, evictTerminalSessions, useTerminalStore } = await import(
  '../../terminal/terminalStore'
);
const { useToastStore } = await import('../../stores/toastStore');
const { TerminalScreen } = await import('./TerminalScreen');

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
  for (const model of Object.values(useTerminalStore.getState().models)) {
    model.dispose();
  }
  useTerminalStore.setState({ models: {}, order: [] });
  useToastStore.setState({ toasts: [] });
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

  const toastKinds = (): string[] =>
    useToastStore.getState().toasts.map((toast) => toast.kind);

  const toastMessage = (): string | undefined =>
    useToastStore.getState().toasts[0]?.message;

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

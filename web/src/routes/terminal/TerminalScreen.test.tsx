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
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PromptTemplate } from '../../api/templateRepository';

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

vi.mock('../../api/templateRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/templateRepository')>();
  return {
    ...actual,
    listTemplates: () => Promise.resolve([alpha]),
    touchTemplate: () => Promise.resolve(),
  };
});

const { sessionChannelName } = await import('../../realtime/channels');
const { acquireTerminal, evictTerminalSessions, useTerminalStore } = await import(
  '../../terminal/terminalStore'
);
const { useToastStore } = await import('../../stores/toastStore');
const { TerminalScreen } = await import('./TerminalScreen');

function renderTerminal(): RenderResult {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={['/agents/a1/sessions/s1']}>
        <Routes>
          <Route
            path="/agents/:agentId/sessions/:sessionId"
            element={<TerminalScreen />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  for (const model of Object.values(useTerminalStore.getState().models)) {
    model.dispose();
  }
  useTerminalStore.setState({ models: {}, order: [] });
  useToastStore.setState({ toasts: [] });
});

describe('TerminalScreen selection toolbar', () => {
  it('mounts the toolbar inside the stage rather than under it', () => {
    // Under the stage the toolbar was a flex sibling in a column whose stage is
    // `flex: 1`, so mounting it took rows off the grid, the fit observer
    // re-measured, and xterm's `SelectionService` clears the selection on any
    // row-count change — the toolbar deleted the selection that summoned it and
    // then unmounted itself. Containment is the half of that fix a test can
    // hold: jsdom computes no layout, so the `position: absolute` that keeps it
    // off the grid inside the stage is left to the stylesheet.
    const model = acquireTerminal('a1', 's1');
    model.state.setState({ hasSelection: true });

    const { container } = renderTerminal();

    const stage = container.querySelector('.terminal-stage');
    expect(stage).toContainElement(screen.getByRole('toolbar', { name: 'Selection' }));
  });
});

describe('TerminalScreen scrollbar', () => {
  it('mounts the slider inside the stage and points it at the surface', () => {
    // Same containment rule as the toolbar, for the same reason — and the
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
});

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
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
  it('hands focus to the composer with the inserted body selected', async () => {
    // The screen owns the insert token, and this is its only producer. The
    // picker's own entry point is the composer's Templates button, so the
    // composer is already mounted and `autoFocus` cannot fire again — without
    // the bump, unmounting the picker drops focus to `document.body` with the
    // body sitting unedited in a field the user is no longer typing into.
    const model = acquireTerminal('a1', 's1');
    model.state.setState({ composerVisible: true });
    renderTerminal();

    fireEvent.click(screen.getByRole('button', { name: 'Templates' }));
    fireEvent.click(await screen.findByRole('button', { name: /body of alpha/ }));

    const field = screen.getByLabelText<HTMLTextAreaElement>('Message');
    await waitFor(() => {
      expect(field).toHaveFocus();
    });
    expect(field.value).toBe(alpha.body);
    expect(field.selectionStart).toBe(0);
    expect(field.selectionEnd).toBe(alpha.body.length);
  });
});

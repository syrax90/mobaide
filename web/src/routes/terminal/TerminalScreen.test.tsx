import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

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

const { acquireTerminal, useTerminalStore } = await import(
  '../../terminal/terminalStore'
);
const { TerminalScreen } = await import('./TerminalScreen');

function renderTerminal(): HTMLElement {
  const { container } = render(
    <MemoryRouter initialEntries={['/agents/a1/sessions/s1']}>
      <Routes>
        <Route
          path="/agents/:agentId/sessions/:sessionId"
          element={<TerminalScreen />}
        />
      </Routes>
    </MemoryRouter>,
  );
  return container;
}

describe('TerminalScreen selection toolbar', () => {
  afterEach(() => {
    cleanup();
    for (const model of Object.values(useTerminalStore.getState().models)) {
      model.dispose();
    }
    useTerminalStore.setState({ models: {}, order: [] });
  });

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

    const container = renderTerminal();

    const stage = container.querySelector('.terminal-stage');
    expect(stage).toContainElement(screen.getByRole('toolbar', { name: 'Selection' }));
  });
});

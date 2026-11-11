import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
// Type-only, so it is erased and cannot defeat the `vi.mock` hoisting below.
import type { ScrollbarMode } from '../../terminal/terminalScrollbar';

// Same seam TerminalScreen.test.tsx uses: a token that never resolves to a
// value keeps `buildUrl` from producing a URL, so the real model is built but
// no socket ever is.
vi.mock('../../auth/agentTokens', () => ({
  ensureAgentToken: () => Promise.resolve(null),
  clearAgentToken: () => undefined,
}));

const { useTerminalStore, acquireTerminal } = await import('../../terminal/terminalStore');
const { TerminalScrollbar } = await import('./TerminalScrollbar');

type Model = ReturnType<typeof acquireTerminal>;

afterEach(() => {
  cleanup();
  for (const model of Object.values(useTerminalStore.getState().models)) {
    model.dispose();
  }
  useTerminalStore.setState({ models: {}, order: [] });
  vi.restoreAllMocks();
});

interface PointerInit {
  button?: number;
  isPrimary?: boolean;
  pointerId?: number;
}

/**
 * jsdom builds no `PointerEvent`, and `fireEvent.pointerDown` then falls back
 * to a bare `Event`, which drops `clientY` — the one field the drag math reads.
 * `button` and `isPrimary` are the two the press gate reads.
 */
function pointer(type: string, clientY: number, init: PointerInit = {}): Event {
  const event = new MouseEvent(type, {
    bubbles: true,
    // So `fireEvent` reports back whether the handler cancelled the press,
    // which is how the focus behaviour below is observable at all.
    cancelable: true,
    clientY,
    button: init.button ?? 0,
  });
  Object.defineProperty(event, 'pointerId', { value: init.pointerId ?? 1 });
  Object.defineProperty(event, 'isPrimary', { value: init.isPrimary ?? true });
  return event;
}

/** jsdom computes no layout, so the two boxes the drag measures are stated. */
function stubBox(node: Element, top: number, height: number): void {
  vi.spyOn(node, 'getBoundingClientRect').mockReturnValue({
    top,
    bottom: top + height,
    height,
    left: 0,
    right: 14,
    width: 14,
    x: 0,
    y: top,
    toJSON: () => ({}),
  });
}

function mount(view: { mode: ScrollbarMode; size: number; offset: number }): {
  model: Model;
  container: HTMLElement;
} {
  const model = acquireTerminal('a1', 's1');
  model.scrollbar.setState(view);
  const { container } = render(<TerminalScrollbar model={model} surfaceId="surface-1" />);
  return { model, container };
}

const scrolled = { mode: 'seek' as const, size: 0.1, offset: 0.5 };
const relative = { mode: 'relative' as const, size: 1, offset: 1 };

describe('TerminalScrollbar', () => {
  it('exposes no control while there is nothing to scroll', () => {
    // The alternate screen, and any session that has not filled a page. The
    // track stays so the reserved gutter never looks like a gap.
    const { container } = mount({ mode: 'idle', size: 1, offset: 1 });

    expect(screen.queryByRole('scrollbar')).toBeNull();
    // Still painted, but out of the hit path — it lies over the terminal, and
    // an alternate-screen program with mouse reporting on has to receive the
    // wheel and pointer events landing in this strip. jsdom resolves no
    // `pointer-events`, so the class is the half a unit test can hold.
    expect(container.querySelector('.terminal-scrollbar')).toHaveClass(
      'terminal-scrollbar-idle',
    );
  });

  it('offers a grip where the program owns the scrolling', () => {
    // The alternate screen and any program holding mouse tracking. There is no
    // position to report, so it is a labelled button rather than a `scrollbar`
    // with a fictional `aria-valuenow`.
    mount(relative);

    expect(screen.queryByRole('scrollbar')).toBeNull();
    const grip = screen.getByRole('button', { name: 'Drag to scroll the terminal' });
    expect(grip).toHaveClass('terminal-scrollbar-grip');
    expect(grip).toHaveAttribute('aria-controls', 'surface-1');
  });

  it('reports travel since the last move, not since the press', () => {
    // The grip is a rate of travel, not a position: measuring from the origin
    // would re-send the whole drag on every move and scroll quadratically.
    const { model } = mount(relative);
    const scroll = vi
      .spyOn(model, 'relativeScrollByPixels')
      .mockImplementation(() => undefined);
    const grip = screen.getByRole('button');

    fireEvent(grip, pointer('pointerdown', 100));
    fireEvent(grip, pointer('pointermove', 130));
    fireEvent(grip, pointer('pointermove', 145));
    fireEvent(grip, pointer('pointermove', 125));

    expect(scroll.mock.calls).toEqual([[30], [15], [-20]]);
  });

  it('stops scrolling once the grip is released', () => {
    const { model } = mount(relative);
    const scroll = vi
      .spyOn(model, 'relativeScrollByPixels')
      .mockImplementation(() => undefined);
    const grip = screen.getByRole('button');

    fireEvent(grip, pointer('pointerdown', 100));
    fireEvent(grip, pointer('pointerup', 130));
    fireEvent(grip, pointer('pointermove', 200));

    expect(scroll).not.toHaveBeenCalled();
  });

  it('keeps gripping when an unrelated finger lifts', () => {
    const { model } = mount(relative);
    const scroll = vi
      .spyOn(model, 'relativeScrollByPixels')
      .mockImplementation(() => undefined);
    const grip = screen.getByRole('button');

    fireEvent(grip, pointer('pointerdown', 100));
    fireEvent(grip, pointer('pointerup', 110, { pointerId: 2, isPrimary: false }));
    fireEvent(grip, pointer('pointermove', 130));

    expect(scroll).toHaveBeenCalledWith(30);
  });

  it('routes the keyboard and the wheel to the program in relative mode', () => {
    const { model } = mount(relative);
    const step = vi.spyOn(model, 'relativeScroll').mockImplementation(() => undefined);
    const page = vi
      .spyOn(model, 'relativeScrollPage')
      .mockImplementation(() => undefined);
    const pixels = vi
      .spyOn(model, 'relativeScrollByPixels')
      .mockImplementation(() => undefined);
    const seekLines = vi
      .spyOn(model, 'scrollByLines')
      .mockImplementation(() => undefined);
    const grip = screen.getByRole('button');

    fireEvent.keyDown(grip, { key: 'ArrowDown' });
    fireEvent.keyDown(grip, { key: 'PageUp' });
    fireEvent.wheel(grip, { deltaY: 90, deltaMode: 0 });
    fireEvent.wheel(grip, { deltaY: -2, deltaMode: 1 });

    expect(step.mock.calls).toEqual([[1], [-2]]);
    expect(page.mock.calls).toEqual([[-1]]);
    expect(pixels.mock.calls).toEqual([[90]]);
    // The seek commands are meaningless here and must not fire.
    expect(seekLines).not.toHaveBeenCalled();
  });

  it('seeks the buffer as the thumb is dragged', () => {
    const { model, container } = mount(scrolled);
    const seek = vi.spyOn(model, 'scrollToOffset').mockImplementation(() => undefined);
    const bar = screen.getByRole('scrollbar');
    const thumb = container.querySelector('.terminal-scrollbar-thumb');
    if (thumb === null) throw new Error('no thumb');
    stubBox(bar, 0, 400);
    stubBox(thumb, 100, 40);

    // Grabbed 20 px down the thumb, then dragged to 200: the thumb top belongs
    // at 180 of the 360 px of travel.
    fireEvent(thumb, pointer('pointerdown', 120));
    fireEvent(thumb, pointer('pointermove', 200));

    expect(seek).toHaveBeenCalledWith(0.5);
  });

  it('stops seeking once the drag ends', () => {
    const { model, container } = mount(scrolled);
    const seek = vi.spyOn(model, 'scrollToOffset').mockImplementation(() => undefined);
    const thumb = container.querySelector('.terminal-scrollbar-thumb');
    if (thumb === null) throw new Error('no thumb');

    fireEvent(thumb, pointer('pointerdown', 120));
    fireEvent(thumb, pointer('pointerup', 200));
    fireEvent(thumb, pointer('pointermove', 300));

    expect(seek).not.toHaveBeenCalled();
  });

  it('pages toward a click on the bare track', () => {
    const { model, container } = mount(scrolled);
    const page = vi.spyOn(model, 'scrollByPages').mockImplementation(() => undefined);
    const bar = screen.getByRole('scrollbar');
    const thumb = container.querySelector('.terminal-scrollbar-thumb');
    if (thumb === null) throw new Error('no thumb');
    stubBox(thumb, 100, 40);

    fireEvent(bar, pointer('pointerdown', 30));
    expect(page).toHaveBeenCalledWith(-1);

    fireEvent(bar, pointer('pointerdown', 300));
    expect(page).toHaveBeenCalledWith(1);
  });

  it('does not page when the gesture started on the thumb', () => {
    // The thumb's own handler stops the event, or every drag would also page.
    const { model, container } = mount(scrolled);
    const page = vi.spyOn(model, 'scrollByPages').mockImplementation(() => undefined);
    const thumb = container.querySelector('.terminal-scrollbar-thumb');
    if (thumb === null) throw new Error('no thumb');
    stubBox(thumb, 100, 40);

    fireEvent(thumb, pointer('pointerdown', 120));

    expect(page).not.toHaveBeenCalled();
  });

  it('does not let a press pull focus off the grid', () => {
    // The div is `tabIndex={0}`, so an un-cancelled press focuses it and every
    // following keystroke is delivered here instead of to the PTY — the
    // terminal reads as dead until the user clicks the grid. Cancelling the
    // press is what a native scrollbar does, and `Tab` still reaches this one.
    const { model, container } = mount(scrolled);
    vi.spyOn(model, 'scrollByPages').mockImplementation(() => undefined);
    const bar = screen.getByRole('scrollbar');
    const thumb = container.querySelector('.terminal-scrollbar-thumb');
    if (thumb === null) throw new Error('no thumb');
    stubBox(thumb, 100, 40);

    expect(fireEvent(bar, pointer('pointerdown', 300))).toBe(false);
    expect(fireEvent(thumb, pointer('pointerdown', 120))).toBe(false);
    expect(bar).toHaveAttribute('tabindex', '0');
  });

  it('leaves a non-primary press for the browser to act on', () => {
    // Cancelling a right-click here would suppress the context menu.
    const { model } = mount(scrolled);
    vi.spyOn(model, 'scrollByPages').mockImplementation(() => undefined);
    const bar = screen.getByRole('scrollbar');

    expect(fireEvent(bar, pointer('pointerdown', 300, { button: 2 }))).toBe(true);
  });

  it('ignores a press that is not the primary button', () => {
    // A right-click opens the context menu; it must not also grab the thumb, or
    // dismissing the menu leaves the viewport somewhere else.
    const { model, container } = mount(scrolled);
    const seek = vi.spyOn(model, 'scrollToOffset').mockImplementation(() => undefined);
    const page = vi.spyOn(model, 'scrollByPages').mockImplementation(() => undefined);
    const bar = screen.getByRole('scrollbar');
    const thumb = container.querySelector('.terminal-scrollbar-thumb');
    if (thumb === null) throw new Error('no thumb');
    stubBox(thumb, 100, 40);

    fireEvent(thumb, pointer('pointerdown', 120, { button: 2 }));
    fireEvent(thumb, pointer('pointermove', 200));
    fireEvent(bar, pointer('pointerdown', 300, { button: 1 }));

    expect(seek).not.toHaveBeenCalled();
    expect(page).not.toHaveBeenCalled();
  });

  it('keeps dragging when an unrelated finger lifts off the thumb', () => {
    // Pointer capture is per-pointer, so a second finger on the widened thumb
    // is hit-tested here normally. Ending the drag on its `pointerup` would
    // strand the finger that is still holding the thumb.
    const { model, container } = mount(scrolled);
    const seek = vi.spyOn(model, 'scrollToOffset').mockImplementation(() => undefined);
    const bar = screen.getByRole('scrollbar');
    const thumb = container.querySelector('.terminal-scrollbar-thumb');
    if (thumb === null) throw new Error('no thumb');
    stubBox(bar, 0, 400);
    stubBox(thumb, 100, 40);

    fireEvent(thumb, pointer('pointerdown', 120));
    fireEvent(thumb, pointer('pointerup', 150, { pointerId: 2, isPrimary: false }));
    fireEvent(thumb, pointer('pointermove', 200));

    expect(seek).toHaveBeenCalledWith(0.5);
  });

  it('ignores a second finger landing during a drag', () => {
    const { model, container } = mount(scrolled);
    const seek = vi.spyOn(model, 'scrollToOffset').mockImplementation(() => undefined);
    const thumb = container.querySelector('.terminal-scrollbar-thumb');
    if (thumb === null) throw new Error('no thumb');

    fireEvent(thumb, pointer('pointerdown', 120, { isPrimary: false }));
    fireEvent(thumb, pointer('pointermove', 200));

    expect(seek).not.toHaveBeenCalled();
  });

  it('scrolls from the keyboard, so the slider is not pointer-only', () => {
    const { model } = mount(scrolled);
    const lines = vi.spyOn(model, 'scrollByLines').mockImplementation(() => undefined);
    const pages = vi.spyOn(model, 'scrollByPages').mockImplementation(() => undefined);
    const top = vi.spyOn(model, 'scrollToTop').mockImplementation(() => undefined);
    const bottom = vi.spyOn(model, 'jumpToBottom').mockImplementation(() => undefined);
    const bar = screen.getByRole('scrollbar');

    fireEvent.keyDown(bar, { key: 'ArrowUp' });
    fireEvent.keyDown(bar, { key: 'ArrowDown' });
    fireEvent.keyDown(bar, { key: 'PageUp' });
    fireEvent.keyDown(bar, { key: 'PageDown' });
    fireEvent.keyDown(bar, { key: 'Home' });
    fireEvent.keyDown(bar, { key: 'End' });

    expect(lines.mock.calls).toEqual([[-1], [1]]);
    expect(pages.mock.calls).toEqual([[-1], [1]]);
    expect(top).toHaveBeenCalledOnce();
    expect(bottom).toHaveBeenCalledOnce();
  });

  it('scrolls on a wheel over the track the viewport no longer receives', () => {
    // Each delta mode keeps its own route: pixels go through the row-height
    // conversion, because collapsing them to a notch made a touchpad run the
    // scrollback many times faster here than over the grid.
    const { model } = mount(scrolled);
    const lines = vi.spyOn(model, 'scrollByLines').mockImplementation(() => undefined);
    const pages = vi.spyOn(model, 'scrollByPages').mockImplementation(() => undefined);
    const pixels = vi.spyOn(model, 'scrollByPixels').mockImplementation(() => undefined);
    const bar = screen.getByRole('scrollbar');

    fireEvent.wheel(bar, { deltaY: 240, deltaMode: 0 });
    fireEvent.wheel(bar, { deltaY: -2, deltaMode: 1 });
    fireEvent.wheel(bar, { deltaY: 1, deltaMode: 2 });
    fireEvent.wheel(bar, { deltaY: 0, deltaMode: 0 });

    expect(pixels.mock.calls).toEqual([[240]]);
    expect(lines.mock.calls).toEqual([[-2]]);
    expect(pages.mock.calls).toEqual([[1]]);
  });
});

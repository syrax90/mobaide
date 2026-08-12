import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

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
}

/**
 * jsdom builds no `PointerEvent`, and `fireEvent.pointerDown` then falls back
 * to a bare `Event`, which drops `clientY` — the one field the drag math reads.
 * `button` and `isPrimary` are the two the press gate reads.
 */
function pointer(type: string, clientY: number, init: PointerInit = {}): Event {
  const event = new MouseEvent(type, {
    bubbles: true,
    clientY,
    button: init.button ?? 0,
  });
  Object.defineProperty(event, 'pointerId', { value: 1 });
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

function mount(view: { scrollable: boolean; size: number; offset: number }): {
  model: Model;
  container: HTMLElement;
} {
  const model = acquireTerminal('a1', 's1');
  model.scrollbar.setState(view);
  const { container } = render(<TerminalScrollbar model={model} surfaceId="surface-1" />);
  return { model, container };
}

const scrolled = { scrollable: true, size: 0.1, offset: 0.5 };

describe('TerminalScrollbar', () => {
  it('exposes no control while there is nothing to scroll', () => {
    // The alternate screen, and any session that has not filled a page. The
    // track stays so the reserved gutter never looks like a gap.
    const { container } = mount({ scrollable: false, size: 1, offset: 1 });

    expect(screen.queryByRole('scrollbar')).toBeNull();
    expect(container.querySelector('.terminal-scrollbar')).not.toBeNull();
  });

  it('publishes its position to assistive technology', () => {
    mount(scrolled);
    const bar = screen.getByRole('scrollbar', { name: 'Terminal scrollback' });

    expect(bar).toHaveAttribute('aria-valuenow', '50');
    expect(bar).toHaveAttribute('aria-orientation', 'vertical');
    // Required on the role, and the surface is the element that scrolls.
    expect(bar).toHaveAttribute('aria-controls', 'surface-1');
  });

  it('hands the thumb geometry to CSS as fractions', () => {
    // Both custom properties, because the stylesheet folds the minimum thumb
    // height into the height and the offset together.
    const { container } = mount(scrolled);
    const thumb = container.querySelector<HTMLElement>('.terminal-scrollbar-thumb');

    expect(thumb?.style.getPropertyValue('--thumb-size')).toBe('10%');
    expect(thumb?.style.getPropertyValue('--thumb-offset')).toBe('0.5');
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

  it('leaves keys it does not own to the browser', () => {
    const { model } = mount(scrolled);
    const lines = vi.spyOn(model, 'scrollByLines').mockImplementation(() => undefined);
    const bar = screen.getByRole('scrollbar');

    expect(fireEvent.keyDown(bar, { key: 'Tab' })).toBe(true);
    expect(lines).not.toHaveBeenCalled();
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

import { describe, expect, it } from 'vitest';
import type { ScrollSample } from './terminalFollow';
import {
  initialScrollbarView,
  lineForOffset,
  offsetFromDrag,
  sameScrollbarView,
  scrollbarView,
  type DragInput,
  type ScrollbarView,
} from './terminalScrollbar';

/** The two buffer indices, in the order xterm reports them. */
const at = (baseY: number, viewportY: number): ScrollSample => ({ baseY, viewportY });

const drag = (overrides: Partial<DragInput> = {}): DragInput => ({
  pointerY: 0,
  trackTop: 0,
  trackHeight: 400,
  thumbHeight: 40,
  grabOffset: 0,
  ...overrides,
});

describe('scrollbarView', () => {
  it('reports nothing to scroll on a buffer with no scrollback', () => {
    // Also the alternate screen, which never accumulates one.
    expect(scrollbarView(at(0, 0), 24)).toEqual({
      scrollable: false,
      size: 1,
      offset: 1,
    });
  });

  it('shrinks the thumb as history accumulates', () => {
    expect(scrollbarView(at(24, 24), 24).size).toBe(0.5);
    expect(scrollbarView(at(72, 72), 24).size).toBe(0.25);
  });

  it('places the thumb by how far the viewport is up the scrollback', () => {
    expect(scrollbarView(at(100, 100), 24).offset).toBe(1);
    expect(scrollbarView(at(100, 50), 24).offset).toBe(0.5);
    expect(scrollbarView(at(100, 0), 24).offset).toBe(0);
  });

  it('reads a mid-reflow overshoot as parked, matching isPinned', () => {
    // `isPinned` accepts `viewportY > baseY` rather than calling it a scroll-up,
    // so the thumb has to agree or it would leave the tail while the badge says
    // the viewport is still on it.
    expect(scrollbarView(at(10, 12), 24).offset).toBe(1);
  });

  it('keeps a usable thumb once the scrollback ring saturates', () => {
    // baseY stops growing at the 10,000-row cap; the fraction has to stay
    // finite and positive rather than collapsing to zero.
    const view = scrollbarView(at(10_000, 5_000), 24);

    expect(view.scrollable).toBe(true);
    expect(view.size).toBeGreaterThan(0);
    expect(view.offset).toBeCloseTo(0.5, 5);
  });

  it('survives a zero-row grid, which is what a pre-open terminal reports', () => {
    expect(scrollbarView(at(0, 0), 0)).toEqual(initialScrollbarView());
  });
});

describe('sameScrollbarView', () => {
  const base: ScrollbarView = { scrollable: true, size: 0.5, offset: 0.5 };

  it('folds a move too small to paint', () => {
    // The throttle that keeps a busy PTY from pushing a store write per row.
    expect(sameScrollbarView(base, { ...base, offset: 0.500_4 })).toBe(true);
  });

  it('reports a move of a thousandth of the track', () => {
    expect(sameScrollbarView(base, { ...base, offset: 0.502 })).toBe(false);
    expect(sameScrollbarView(base, { ...base, size: 0.502 })).toBe(false);
  });

  it('never folds the appearance or loss of a scrollback', () => {
    expect(sameScrollbarView(base, { ...base, scrollable: false })).toBe(false);
  });
});

describe('offsetFromDrag', () => {
  it('maps the pointer across the thumb travel, not the whole track', () => {
    // 400 track - 40 thumb = 360 of travel; half of it is the middle.
    expect(offsetFromDrag(drag({ pointerY: 180 }))).toBe(0.5);
    expect(offsetFromDrag(drag({ pointerY: 360 }))).toBe(1);
  });

  it('holds the grab point under the pointer', () => {
    // Grabbed 30 px down the thumb, so the thumb top belongs 30 px above the
    // pointer — without this the thumb jumps so its top meets the finger.
    expect(offsetFromDrag(drag({ pointerY: 210, grabOffset: 30 }))).toBe(0.5);
  });

  it('measures from the track, not the viewport', () => {
    expect(offsetFromDrag(drag({ pointerY: 280, trackTop: 100 }))).toBe(0.5);
  });

  it('clamps past either end of the travel', () => {
    expect(offsetFromDrag(drag({ pointerY: -50 }))).toBe(0);
    expect(offsetFromDrag(drag({ pointerY: 9_000 }))).toBe(1);
  });

  it('parks at the tail when there is nowhere to travel', () => {
    // The CSS minimum thumb height can exceed the track on a very short stage,
    // which makes the divisor negative rather than merely zero.
    expect(offsetFromDrag(drag({ trackHeight: 40, thumbHeight: 40 }))).toBe(1);
    expect(offsetFromDrag(drag({ trackHeight: 20, thumbHeight: 40 }))).toBe(1);
  });
});

describe('lineForOffset', () => {
  it('names the buffer line the offset points at', () => {
    expect(lineForOffset(0, 500)).toBe(0);
    expect(lineForOffset(0.5, 500)).toBe(250);
    expect(lineForOffset(1, 500)).toBe(500);
  });

  it('rounds to a whole line', () => {
    expect(lineForOffset(0.5, 501)).toBe(251);
  });

  it('clamps a stale offset from a drag that outran the buffer', () => {
    expect(lineForOffset(-0.2, 500)).toBe(0);
    expect(lineForOffset(1.4, 500)).toBe(500);
  });

  it('is the top of the buffer when there is no scrollback', () => {
    expect(lineForOffset(0.5, 0)).toBe(0);
  });
});

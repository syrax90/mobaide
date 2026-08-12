import { describe, expect, it } from 'vitest';
import type { ScrollSample } from './terminalFollow';
import {
  initialScrollbarView,
  lineForOffset,
  offsetFromDrag,
  relativeSteps,
  sameScrollbarView,
  scrollbarView,
  wheelRows,
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

/** The emulator states the ladder branches on. */
const PLAIN = { altBuffer: false, mouseReporting: false };
const ALT = { altBuffer: true, mouseReporting: false };
const TRACKING = { altBuffer: false, mouseReporting: true };

describe('scrollbarView modes', () => {
  it('seeks whenever there is scrollback to seek through', () => {
    expect(scrollbarView(at(100, 50), 24, PLAIN).mode).toBe('seek');
  });

  it('keeps seeking even while a program holds mouse tracking', () => {
    // `scrollToLine` still works on the primary buffer, and a visible thumb
    // promises a position — so it has to keep meaning one.
    expect(scrollbarView(at(100, 50), 24, TRACKING).mode).toBe('seek');
  });

  it('hands the gesture to the program on the alternate screen', () => {
    // The whole reason this mode exists: Claude Code, Codex, vim and less all
    // run here, where the buffer has no scrollback and never will.
    expect(scrollbarView(at(0, 0), 24, ALT).mode).toBe('relative');
  });

  it('hands it over for mouse tracking without a scrollback too', () => {
    expect(scrollbarView(at(0, 0), 24, TRACKING).mode).toBe('relative');
  });

  it('goes idle only when nothing can scroll and nobody wants the wheel', () => {
    expect(scrollbarView(at(0, 0), 24, PLAIN)).toEqual(initialScrollbarView());
    expect(initialScrollbarView().mode).toBe('idle');
  });
});

describe('scrollbarView geometry', () => {
  it('shrinks the thumb as history accumulates', () => {
    expect(scrollbarView(at(24, 24), 24, PLAIN).size).toBe(0.5);
    expect(scrollbarView(at(72, 72), 24, PLAIN).size).toBe(0.25);
  });

  it('places the thumb by how far the viewport is up the scrollback', () => {
    expect(scrollbarView(at(100, 100), 24, PLAIN).offset).toBe(1);
    expect(scrollbarView(at(100, 50), 24, PLAIN).offset).toBe(0.5);
    expect(scrollbarView(at(100, 0), 24, PLAIN).offset).toBe(0);
  });

  it('reads a mid-reflow overshoot as parked, matching isPinned', () => {
    // `isPinned` accepts `viewportY > baseY` rather than calling it a scroll-up,
    // so the thumb has to agree or it would leave the tail while the badge says
    // the viewport is still on it.
    expect(scrollbarView(at(10, 12), 24, PLAIN).offset).toBe(1);
  });

  it('keeps a usable thumb once the scrollback ring saturates', () => {
    // baseY stops growing at the 10,000-row cap; the fraction has to stay
    // finite and positive rather than collapsing to zero.
    const view = scrollbarView(at(10_000, 5_000), 24, PLAIN);

    expect(view.mode).toBe('seek');
    expect(view.size).toBeGreaterThan(0);
    expect(view.offset).toBeCloseTo(0.5, 5);
  });

  it('survives a zero-row grid, which is what a pre-open terminal reports', () => {
    expect(scrollbarView(at(0, 0), 0, PLAIN)).toEqual(initialScrollbarView());
  });
});

describe('sameScrollbarView', () => {
  const base: ScrollbarView = { mode: 'seek', size: 0.5, offset: 0.5 };

  it('folds a move too small to paint', () => {
    // The throttle that keeps a busy PTY from pushing a store write per row.
    expect(sameScrollbarView(base, { ...base, offset: 0.500_4 })).toBe(true);
  });

  it('reports a move of a thousandth of the track', () => {
    expect(sameScrollbarView(base, { ...base, offset: 0.502 })).toBe(false);
    expect(sameScrollbarView(base, { ...base, size: 0.502 })).toBe(false);
  });

  it('never folds a change of mode', () => {
    expect(sameScrollbarView(base, { ...base, mode: 'relative' })).toBe(false);
    expect(sameScrollbarView(base, { ...base, mode: 'idle' })).toBe(false);
  });

  it('ignores geometry outside seek mode, where nothing paints from it', () => {
    const grip: ScrollbarView = { mode: 'relative', size: 1, offset: 1 };
    expect(sameScrollbarView(grip, { ...grip, offset: 0.2, size: 0.3 })).toBe(true);
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

describe('wheelRows', () => {
  const CELL = 15;

  it('spends a touchpad flick at the same rate as one large delta', () => {
    // The regression this exists for: 20 events of 10 px each used to be worth
    // one notch apiece, 60 rows for 200 px of travel. The distance is physical,
    // so 200 px is 200/15 rows however it is chopped up — and a stream of small
    // deltas must not come out slower than a single big one either.
    let carry = 0;
    let total = 0;
    for (let event = 0; event < 20; event += 1) {
      const step = wheelRows(10, CELL, carry);
      carry = step.carry;
      total += step.rows;
    }

    expect(total).toBe(13);
    expect(total).toBe(wheelRows(200, CELL, 0).rows);
  });

  it('spends a large delta at one row per cell height', () => {
    expect(wheelRows(150, CELL, 0).rows).toBe(10);
  });

  it('carries the sub-row remainder instead of rounding it away', () => {
    // 60/15 = 4 exactly; 70/15 = 4 with 2/3 of a row left over, and the next
    // event has to spend it or the wheel drifts slower than the content.
    const first = wheelRows(70, CELL, 0);

    expect(first.rows).toBe(4);
    expect(first.carry).toBeCloseTo(2 / 3, 5);
    expect(wheelRows(70, CELL, first.carry).rows).toBe(5);
  });

  it('scrolls up on a negative delta', () => {
    expect(wheelRows(-150, CELL, 0).rows).toBe(-10);
  });

  it('drops a carry left pointing the other way', () => {
    // It belongs to a gesture that already ended; subtracting it would eat the
    // first rows of the reversal.
    expect(wheelRows(-150, CELL, 0.9).rows).toBe(-10);
  });

  it('refuses to divide by an unmeasured grid', () => {
    // Before the host is laid out; nothing has entered the scrollback yet
    // either, so there is nothing to scroll to.
    expect(wheelRows(120, 0, 0)).toEqual({ rows: 0, carry: 0 });
    expect(wheelRows(0, CELL, 0.4)).toEqual({ rows: 0, carry: 0.4 });
  });
});

describe('relativeSteps', () => {
  it('emits one step per row', () => {
    expect(relativeSteps(5, 24)).toBe(5);
    expect(relativeSteps(-5, 24)).toBe(5);
  });

  it('caps a fast drag at one screen', () => {
    // Every step is a wheel event crossing a WebSocket and a relay, where
    // mobile's PTY is local — iOS caps the same way, Android does not cap.
    expect(relativeSteps(500, 24)).toBe(24);
  });

  it('still moves on a grid that reports no rows', () => {
    expect(relativeSteps(3, 0)).toBe(1);
  });

  it('does nothing for a zero or non-finite request', () => {
    expect(relativeSteps(0, 24)).toBe(0);
    expect(relativeSteps(Number.NaN, 24)).toBe(0);
    expect(relativeSteps(0.4, 24)).toBe(0);
  });
});

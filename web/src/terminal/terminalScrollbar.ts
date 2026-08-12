/**
 * Geometry for the scrollback slider, in the same two buffer indices the follow
 * math reads.
 *
 * The track lives in a gutter that already exists: `FitAddon` reserves the
 * scrollbar width as the constant `options.overviewRuler?.width || 14` whenever
 * `scrollback` is non-zero — it measures no real bar, and xterm 6 dropped
 * `scrollBarWidth` entirely. So the grid is inset by exactly that much
 * regardless, and putting the slider there costs no columns and triggers no
 * reflow. `--terminal-scrollbar-width` in `global.css` is the other half of that
 * pair; keep the two equal across an addon bump.
 *
 * Rendering takes no measurement: {@link ScrollbarView} is two fractions the
 * component hands to CSS as custom properties, which is what keeps this
 * testable under jsdom, where nothing has a layout. Only a drag needs real
 * pixels, and that arithmetic is {@link offsetFromDrag}.
 */

import type { ScrollSample } from './terminalFollow';

/**
 * Publish resolution, as a divisor of the track.
 *
 * {@link sameScrollbarView} is the throttle on a hot path: the model samples on
 * every parsed write and every scroll event, and an un-quantized diff would
 * push a store write — and a React render — per row a busy PTY prints. A
 * thousandth of the track is below one pixel on any real terminal.
 */
const PUBLISH_STEPS = 1000;

export interface ScrollbarView {
  /** Whether anything sits above the viewport. False on the alternate screen. */
  scrollable: boolean;
  /** Fraction of the track the thumb fills, 0..1. */
  size: number;
  /** Fraction of the thumb's free travel that is above it, 0..1. 1 is the tail. */
  offset: number;
}

export function initialScrollbarView(): ScrollbarView {
  return { scrollable: false, size: 1, offset: 1 };
}

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

/**
 * Thumb size and position for one buffer sample.
 *
 * `baseY + rows` rather than a scrollback constant: `baseY` is what the buffer
 * actually holds, so the thumb grows from full-height to its final size as
 * history accumulates and then stops moving once the ring saturates.
 */
export function scrollbarView(sample: ScrollSample, rows: number): ScrollbarView {
  // A full thumb parked at the tail, which is what a scrollbar over content
  // that fits shows. Also the alternate screen, and a grid that has not been
  // measured yet.
  if (sample.baseY <= 0) return initialScrollbarView();
  return {
    scrollable: true,
    size: clamp01(rows / (sample.baseY + rows)),
    // `isPinned` reads a mid-reflow overshoot as parked rather than as a
    // scroll-up, so clamping here keeps the thumb agreeing with the badge.
    offset: clamp01(sample.viewportY / sample.baseY),
  };
}

/** Whether two views would paint the same thumb. See {@link PUBLISH_STEPS}. */
export function sameScrollbarView(a: ScrollbarView, b: ScrollbarView): boolean {
  return (
    a.scrollable === b.scrollable &&
    Math.round(a.size * PUBLISH_STEPS) === Math.round(b.size * PUBLISH_STEPS) &&
    Math.round(a.offset * PUBLISH_STEPS) === Math.round(b.offset * PUBLISH_STEPS)
  );
}

export interface DragInput {
  /** Pointer position in client coordinates. */
  pointerY: number;
  /** Track top edge in client coordinates. */
  trackTop: number;
  trackHeight: number;
  /** Rendered thumb height, which CSS may have raised to its minimum. */
  thumbHeight: number;
  /** Where inside the thumb the drag started, so it does not jump to the finger. */
  grabOffset: number;
}

/**
 * Where the pointer puts the thumb, as an offset fraction.
 *
 * The thumb travels `trackHeight - thumbHeight`, not the whole track. A short
 * scrollback can leave that at or below zero — the CSS minimum thumb height
 * makes it genuinely negative — and there is nowhere to drag to, so the answer
 * is the tail.
 */
export function offsetFromDrag(input: DragInput): number {
  const travel = input.trackHeight - input.thumbHeight;
  if (travel <= 0) return 1;
  return clamp01((input.pointerY - input.trackTop - input.grabOffset) / travel);
}

/** The absolute buffer line an offset names. */
export function lineForOffset(offset: number, baseY: number): number {
  if (baseY <= 0) return 0;
  return Math.round(clamp01(offset) * baseY);
}

export interface WheelRows {
  rows: number;
  /** Sub-row remainder, carried into the next event. */
  carry: number;
}

/**
 * Rows a pixel-mode wheel delta is worth: a row per cell height, exactly.
 *
 * A precision touchpad emits dozens of events per flick, so treating each one
 * as a notch raced through the scrollback at many times the speed the same
 * gesture gets over the grid. The distance is physical, though, so the
 * conversion is the plain ratio and the whole job is not throwing away the
 * sub-row remainder — xterm 6 scrolls through a VS Code `ScrollableElement`,
 * which moves the content in pixels, so a pixel here is a pixel there.
 *
 * Nothing is damped. `CoreMouseService.consumeWheelEvent` does scale small
 * deltas, but it governs wheel-to-mouse *reporting* — its only two call sites
 * are the mouse-tracking path and the no-scrollback arrow-key path, neither of
 * which is this one.
 *
 * A carry pointing the other way is dropped rather than subtracted: it belongs
 * to a gesture that has already ended, and applying it would eat the first rows
 * of the reversal.
 */
export function wheelRows(deltaY: number, cellHeight: number, carry: number): WheelRows {
  // `cellHeight` is 0 until the host has been laid out. Nothing has scrolled
  // into the buffer by then either, so refusing is the whole handling.
  if (deltaY === 0 || cellHeight <= 0) return { rows: 0, carry };
  const amount = deltaY / cellHeight;
  const total = carry > 0 === amount > 0 ? carry + amount : amount;
  const rows = Math.trunc(total);
  return { rows, carry: total - rows };
}

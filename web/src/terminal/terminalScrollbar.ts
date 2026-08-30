/**
 * Geometry for the scrollback slider, in the same two buffer indices the follow
 * math reads.
 *
 * The track lives in a gutter that already exists: `FitAddon` reserves the
 * scrollbar width as the constant `options.overviewRuler?.width || 14` whenever
 * `scrollback` is non-zero — it measures no real bar, and xterm 6 dropped
 * `scrollBarWidth` entirely. So the grid is inset by exactly that much
 * regardless, and putting the slider there costs no columns and triggers no
 * reflow. `--terminal-scrollbar-width` in `global.css` and the exported
 * {@link SCROLLBAR_GUTTER_PX} the create-time grid estimate divides by are the
 * other two members; keep all three equal across an addon bump.
 *
 * Rendering takes no measurement: {@link ScrollbarView} is two fractions the
 * component hands to CSS as custom properties, which is what keeps this
 * testable under jsdom, where nothing has a layout. Only a drag needs real
 * pixels, and that arithmetic is {@link offsetFromDrag}.
 */

/** The gutter `FitAddon` reserves (`options.overviewRuler?.width || 14`). */
export const SCROLLBAR_GUTTER_PX = 14;

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

/**
 * What the control can do, which is the web spelling of the mobile scroll
 * ladder (iOS `TerminalWheelScroll.mode`, Android `doScroll`).
 *
 * The arms differ from mobile's only in who performs them: xterm already turns
 * a wheel event into a mouse report while a program has tracking on, and into
 * a DECCKM-correct arrow key on the alternate screen, so `relative` covers both
 * of mobile's top two arms and the control never encodes a byte itself.
 */
export type ScrollbarMode =
  /** Scrollback exists: the thumb shows where the viewport is and seeks to a line. */
  | 'seek'
  /**
   * No scrollback, but scrolling still means something — the program owns it.
   * The grip has no position to show, so it reports travel instead.
   */
  | 'relative'
  /** Nothing to scroll and nobody to ask. */
  | 'idle';

export interface ScrollbarView {
  mode: ScrollbarMode;
  /** Fraction of the track the thumb fills, 0..1. `seek` only. */
  size: number;
  /** Fraction of the thumb's free travel that is above it, 0..1. `seek` only. */
  offset: number;
}

export function initialScrollbarView(): ScrollbarView {
  return { mode: 'idle', size: 1, offset: 1 };
}

/** The emulator state the ladder reads, in xterm's public terms. */
export interface TerminalScrollState {
  /** `buffer.active.type === 'alternate'` — no scrollback exists there. */
  altBuffer: boolean;
  /** `modes.mouseTrackingMode !== 'none'` — the program wants the wheel. */
  mouseReporting: boolean;
}

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

/**
 * Which arm of the ladder applies, and the thumb geometry if it is `seek`.
 *
 * Scrollback wins over the program: `scrollToLine` still works on the primary
 * buffer even while a program has mouse tracking on, and a visible thumb
 * promises a position, so it has to keep meaning one. Only when there is no
 * position to show does the control hand the gesture to the program.
 *
 * `baseY + rows` rather than a scrollback constant: `baseY` is what the buffer
 * actually holds, so the thumb grows from full-height to its final size as
 * history accumulates and then stops moving once the ring saturates.
 */
export function scrollbarView(
  sample: ScrollSample,
  rows: number,
  state: TerminalScrollState,
): ScrollbarView {
  if (sample.baseY <= 0) {
    // The alternate screen has no scrollback by construction, and a program
    // with tracking on takes the wheel even on the primary buffer — in both
    // cases scrolling works, it just is not ours to position.
    const mode = state.altBuffer || state.mouseReporting ? 'relative' : 'idle';
    return { ...initialScrollbarView(), mode };
  }
  return {
    mode: 'seek',
    size: clamp01(rows / (sample.baseY + rows)),
    // `isPinned` reads a mid-reflow overshoot as parked rather than as a
    // scroll-up, so clamping here keeps the thumb agreeing with the badge.
    offset: clamp01(sample.viewportY / sample.baseY),
  };
}

/** Whether two views would paint the same control. See {@link PUBLISH_STEPS}. */
export function sameScrollbarView(a: ScrollbarView, b: ScrollbarView): boolean {
  if (a.mode !== b.mode) return false;
  // Only `seek` paints from these; the grip is parked wherever the mode says.
  if (a.mode !== 'seek') return true;
  return (
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

/**
 * Steps a relative scroll of `rows` should emit, capped at one screen.
 *
 * Each step is a whole wheel event crossing a WebSocket and a relay, where
 * mobile's PTY is local — iOS caps the same way for the same reason, Android
 * does not cap at all. A fast drag that outruns the cap simply stops at a page;
 * the next move continues from there.
 */
export function relativeSteps(rows: number, pageRows: number): number {
  if (!Number.isFinite(rows) || rows === 0) return 0;
  return Math.min(Math.abs(Math.trunc(rows)), Math.max(1, pageRows));
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

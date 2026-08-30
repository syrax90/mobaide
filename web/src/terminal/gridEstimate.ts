/**
 * The grid a terminal will have, computed before one exists.
 *
 * A session's PTY is spawned by the create request, before the terminal route
 * has mounted and long before xterm has measured itself. Getting the width
 * wrong is not self-correcting: the program lays its opening frames out at the
 * spawn width, those bytes enter the agent's replay ring, and every later
 * reconnect re-renders them at the real grid.
 *
 * This is an estimate. xterm derives its cell from the renderer's own char
 * atlas after `open()`, which cannot be consulted here, so we measure the same
 * font ourselves. Being a cell or two out costs nothing — the terminal asserts
 * its real grid within a frame of mounting; what matters is not shipping 80
 * columns to a viewport that is nothing like 80 columns wide.
 */

import { SCROLLBAR_GUTTER_PX } from './terminalScrollbar';

/** The face the terminal is built with. Owned here so the estimate and the
 * terminal it predicts cannot disagree — `sessionModel.ts` reads these. */
export const TERMINAL_FONT_FAMILY =
  'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace';
export const TERMINAL_FONT_SIZE = 13;

/** xterm's own floor; also what `FitAddon` will never propose below. */
const MIN_DIMENSION = 2;


export interface GridEstimate {
  rows: number;
  cols: number;
}

/** Fallback when nothing can be measured (SSR, a hidden document, no canvas). */
export const FALLBACK_GRID: GridEstimate = { rows: 24, cols: 80 };

function measureCellWidth(): number | null {
  try {
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    if (ctx === null) return null;
    ctx.font = `${TERMINAL_FONT_SIZE}px ${TERMINAL_FONT_FAMILY}`;
    // 'W' is what SwiftTerm measures; any glyph does in a monospace face, and
    // the fallback stack is monospace by construction.
    const width = ctx.measureText('W').width;
    return width > 0 ? width : null;
  } catch {
    return null;
  }
}

/**
 * Estimate the grid for a terminal filling `viewport`.
 *
 * `lineHeightRatio` matches xterm's default `lineHeight: 1` against a typical
 * monospace ascent+descent — near enough for rows, which do not affect wrapping
 * and are corrected on mount.
 */
export function estimateGrid(
  viewport: { width: number; height: number },
  cellWidth: number | null = measureCellWidth(),
  lineHeightRatio = 1.2,
): GridEstimate {
  if (cellWidth === null || viewport.width <= 0 || viewport.height <= 0) {
    return FALLBACK_GRID;
  }
  const cellHeight = TERMINAL_FONT_SIZE * lineHeightRatio;
  return {
    cols: Math.max(MIN_DIMENSION, Math.floor((viewport.width - SCROLLBAR_GUTTER_PX) / cellWidth)),
    rows: Math.max(MIN_DIMENSION, Math.floor(viewport.height / cellHeight)),
  };
}

/** The estimate for the current window. */
export function currentGridEstimate(): GridEstimate {
  if (typeof window === 'undefined' || typeof document === 'undefined') return FALLBACK_GRID;
  return estimateGrid({ width: window.innerWidth, height: window.innerHeight });
}

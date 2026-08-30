import { describe, expect, it } from 'vitest';
import { estimateGrid, FALLBACK_GRID } from './gridEstimate';

/**
 * The agent spawns the PTY at whatever grid the create request names, and the
 * program lays its opening frames out there. Those bytes then sit in the
 * agent's replay ring with no width attached, so an estimate that is wrong by
 * more than a cell or two is not self-correcting — every later reconnect
 * re-renders them at the real grid.
 *
 * `estimateGrid` takes its cell width as an argument precisely so this can be
 * pinned without a canvas (jsdom has none), which is also why the fallback arm
 * needs its own case rather than being inferred from the environment.
 */

/** A plausible measurement for the 13px monospace stack, chosen to divide evenly. */
const CELL_WIDTH = 8;

describe('estimateGrid', () => {
  it('divides the viewport by the measured cell', () => {
    const grid = estimateGrid({ width: 800, height: 600 }, CELL_WIDTH);

    // (800 - 14px gutter) / 8 = 98.25 → 98; 600 / (13 * 1.2) = 38.46 → 38.
    expect(grid).toEqual({ cols: 98, rows: 38 });
  });

  it('subtracts the scrollbar gutter rather than claiming the full width', () => {
    // 814 / 8 is exactly 101 columns, but 14px of that belongs to the gutter
    // FitAddon will reserve. Claiming the extra column is the failure mode that
    // matters: the PTY wraps a column later than the terminal renders.
    const grid = estimateGrid({ width: 814, height: 600 }, CELL_WIDTH);

    expect(grid.cols).toBe(100);
  });

  it('falls back when the font cannot be measured', () => {
    // No canvas (SSR, jsdom, a locked-down document) — better a known default
    // than a grid derived from a made-up cell.
    expect(estimateGrid({ width: 800, height: 600 }, null)).toEqual(FALLBACK_GRID);
  });

  it('falls back for a viewport with no extent', () => {
    // A hidden or not-yet-laid-out document reports 0. Without the guard the
    // gutter subtraction goes negative and the session spawns at the floor.
    expect(estimateGrid({ width: 0, height: 600 }, CELL_WIDTH)).toEqual(FALLBACK_GRID);
    expect(estimateGrid({ width: 800, height: 0 }, CELL_WIDTH)).toEqual(FALLBACK_GRID);
  });

  it('floors both axes instead of proposing a zero-dimension grid', () => {
    // A viewport narrower than the gutter itself still has a positive width, so
    // it clears the guard above and reaches the arithmetic: (10 - 14) / 8 is
    // negative, and 5 / 15.6 truncates to zero.
    const grid = estimateGrid({ width: 10, height: 5 }, CELL_WIDTH);

    expect(grid).toEqual({ cols: 2, rows: 2 });
  });
});

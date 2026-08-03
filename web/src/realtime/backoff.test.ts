import { describe, expect, it } from 'vitest';
import { Backoff, INITIAL_BACKOFF_MS } from './backoff';

/** The RNG is injected, so every delay in this file is exact rather than a range. */
function ladder(backoff: Backoff, steps: number): number[] {
  return Array.from({ length: steps }, () => backoff.next());
}

describe('Backoff', () => {
  it('walks the mobile ladder and holds at the 30s cap', () => {
    const backoff = new Backoff(() => 1);

    expect(ladder(backoff, 8)).toEqual([
      1000, 2000, 4000, 8000, 16000, 30_000, 30_000, 30_000,
    ]);
  });

  it('never sleeps longer than the mobile delay for the same attempt', () => {
    // Jitter is a 50-100 % multiplier of the ladder value, so the floor is half
    // the mobile delay and the ceiling is the mobile delay itself.
    expect(ladder(new Backoff(() => 0), 3)).toEqual([500, 1000, 2000]);
  });

  it('advances the ladder by the undoubled value, not the jittered one', () => {
    const backoff = new Backoff(() => 0.5);

    expect(backoff.next()).toBe(750);
    expect(backoff.baseDelayMs).toBe(2000);
  });

  it('returns to the first rung after reset', () => {
    const backoff = new Backoff(() => 1);
    ladder(backoff, 4);

    backoff.reset();

    expect(backoff.baseDelayMs).toBe(INITIAL_BACKOFF_MS);
    expect(backoff.next()).toBe(1000);
  });
});

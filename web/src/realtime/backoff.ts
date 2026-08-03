/**
 * Reconnect backoff, ported from Android `WebSocketConnection.scheduleReconnect`
 * and iOS `WebSocketCore`.
 *
 * The mobile ladder sleeps the *current* delay and then doubles it, capped at
 * 30 s: 1000, 2000, 4000, 8000, 16000, 30000, 30000… A successful open (and
 * `forceReconnect()`) resets it to 1 s.
 *
 * Web adds 50–100 % jitter on top, which the mobile clients do not have. A
 * browser user routinely has the app open in several tabs, and a relay restart
 * would otherwise bring them all back in lockstep; jitter spreads the herd.
 * Applying it as a multiplier of the ladder value means the web delay is never
 * *longer* than the mobile one for the same attempt.
 */
export const INITIAL_BACKOFF_MS = 1000;
export const MAX_BACKOFF_MS = 30_000;

export class Backoff {
  private base = INITIAL_BACKOFF_MS;
  private readonly random: () => number;

  constructor(random: () => number = Math.random) {
    this.random = random;
  }

  /** Undoubled ladder value the next `next()` will jitter — exposed for tests. */
  get baseDelayMs(): number {
    return this.base;
  }

  reset(): void {
    this.base = INITIAL_BACKOFF_MS;
  }

  /** Jittered delay for this attempt; advances the ladder as a side effect. */
  next(): number {
    const delay = this.base * (0.5 + this.random() * 0.5);
    this.base = Math.min(this.base * 2, MAX_BACKOFF_MS);
    return Math.round(delay);
  }
}

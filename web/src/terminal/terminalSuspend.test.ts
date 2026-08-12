import { describe, expect, it } from 'vitest';
import {
  FIREHOSE_BYTES,
  GRACE_MS,
  RECOUP_BYTES,
  shouldSuspend,
} from './terminalSuspend';

describe('shouldSuspend', () => {
  it('keeps a quiet session attached however long it goes unread', () => {
    // The whole point of the byte term: an idle session costs nothing to hold,
    // and suspending it would charge a full ring replay to save that nothing.
    expect(
      shouldSuspend({ unwatchedMs: GRACE_MS * 100, bytes: RECOUP_BYTES - 1 }),
    ).toBe(false);
  });

  it('keeps a busy session attached inside the grace window', () => {
    // The time term. An alt-tab is not worth a replay even on a chatty session.
    expect(
      shouldSuspend({ unwatchedMs: GRACE_MS - 1, bytes: RECOUP_BYTES * 4 }),
    ).toBe(false);
  });

  it('suspends once the session has both gone unread and spent a ring', () => {
    expect(shouldSuspend({ unwatchedMs: GRACE_MS, bytes: RECOUP_BYTES })).toBe(true);
  });

  it('cuts a firehose off without waiting out the grace', () => {
    expect(shouldSuspend({ unwatchedMs: 0, bytes: FIREHOSE_BYTES })).toBe(true);
  });

  it('holds its boundaries inclusive on every term', () => {
    // Pinned so a refactor cannot silently turn a `>=` into a `>` and shift the
    // policy by one byte or one millisecond.
    expect(shouldSuspend({ unwatchedMs: GRACE_MS, bytes: RECOUP_BYTES - 1 })).toBe(
      false,
    );
    expect(shouldSuspend({ unwatchedMs: GRACE_MS - 1, bytes: FIREHOSE_BYTES })).toBe(
      true,
    );
  });

  it('prices the recoup threshold at one ring and the firehose above it', () => {
    // The constants are the policy. `RECOUP_BYTES` below the 4 MiB ring would
    // make every suspend a guaranteed net loss.
    expect(RECOUP_BYTES).toBe(4 * 1024 * 1024);
    expect(GRACE_MS).toBe(60_000);
    expect(FIREHOSE_BYTES).toBeGreaterThan(RECOUP_BYTES);
  });
});

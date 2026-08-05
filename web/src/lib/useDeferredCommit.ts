/**
 * A destructive action that is *scheduled* rather than sent, so Undo cancels it
 * before anything reaches the server.
 *
 * This is the model git delete already uses on Android and iOS, and the one web
 * also uses for project delete — instead of mobile's delete-then-recreate, whose
 * compensating `POST` cannot restore a project's `linear_api_key` (the server
 * never echoes it) or its position in the order. Android and iOS follow in
 * CLA-240 / CLA-241.
 *
 * Three ways a window ends, all of them mobile's:
 * - the timer elapses → commit;
 * - the user dismisses the bar → commit **now** (Android's `commitNow()`);
 * - the user hits Undo → cancel, and nothing was ever sent.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

/** Both mobile clients' `UNDO_WINDOW_MS`. */
export const UNDO_WINDOW_MS = 5_000;

export interface DeferredCommit<T> {
  /** The scheduled payload, or `null` when no window is open. */
  pending: T | null;
  /**
   * Open a window, or replace an open one. `merge` decides what happens to a
   * payload scheduled while a window is already open: git coalesces into one
   * batch, projects commit the first and start over.
   */
  schedule: (payload: T) => void;
  /** Send now, ending the window early. */
  commitNow: () => void;
  /** Drop the payload unsent. */
  cancel: () => void;
}

export interface DeferredCommitOptions<T> {
  onCommit: (payload: T) => void;
  /**
   * How a new payload combines with one already scheduled. Returning
   * `{ commit, next }` lets a caller flush the old payload (`commit`) before
   * queuing the new one. Default: coalescing is not attempted — the previous
   * payload is committed and the new one starts a fresh window.
   */
  merge?: (previous: T, next: T) => { commit: T | null; next: T };
  windowMs?: number;
}

export function useDeferredCommit<T>(
  options: DeferredCommitOptions<T>,
): DeferredCommit<T> {
  const [pending, setPending] = useState<T | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // The payload lives in a ref as well as in state, so the timer callback reads
  // the value at fire time rather than the one captured when it was armed. That
  // is what lets a coalescing `schedule` restart the window without the earlier
  // payload being stranded inside a superseded closure — the invariant
  // `GitScreenModel` spells out for `pendingBatch`.
  const payloadRef = useRef<T | null>(null);
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const clearTimer = useCallback(() => {
    if (timer.current !== null) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  const flush = useCallback(() => {
    clearTimer();
    const payload = payloadRef.current;
    payloadRef.current = null;
    setPending(null);
    if (payload !== null) optionsRef.current.onCommit(payload);
  }, [clearTimer]);

  const schedule = useCallback(
    (payload: T) => {
      const previous = payloadRef.current;
      let queued = payload;
      if (previous !== null) {
        const merge = optionsRef.current.merge;
        if (merge === undefined) {
          // No merge strategy: the earlier payload is committed rather than
          // dropped. Losing a delete the user already confirmed would be worse
          // than sending it a moment early.
          clearTimer();
          payloadRef.current = null;
          optionsRef.current.onCommit(previous);
        } else {
          const merged = merge(previous, payload);
          queued = merged.next;
          if (merged.commit !== null) {
            clearTimer();
            optionsRef.current.onCommit(merged.commit);
          }
        }
      }
      clearTimer();
      payloadRef.current = queued;
      setPending(queued);
      timer.current = setTimeout(flush, optionsRef.current.windowMs ?? UNDO_WINDOW_MS);
    },
    [clearTimer, flush],
  );

  const cancel = useCallback(() => {
    clearTimer();
    payloadRef.current = null;
    setPending(null);
  }, [clearTimer]);

  // Unmount cancels rather than commits. Navigating away is not consent to a
  // delete the user still had five seconds to take back, and the row is still
  // there on the server — the next list load shows it again.
  useEffect(() => cancel, [cancel]);

  return { pending, schedule, commitNow: flush, cancel };
}

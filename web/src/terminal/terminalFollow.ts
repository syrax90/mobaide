/**
 * Follow mode and the unread tally, expressed entirely in xterm's public buffer
 * indices.
 *
 * **Follow state is derived, never commanded.** Pinned ⇔ `viewportY === baseY`,
 * which is exactly the negation of xterm's internal `isUserScrolling`. That is
 * also what makes the web client immune to the bug both mobile clients had to
 * fix by hand (Android CLA-184, iOS CLA-178): xterm scrolls to the bottom on
 * *genuine* user input only, because `scrollOnUserInput` is gated on the
 * internal `wasUserInput` flag that emulator auto-replies — `ESC[6n` from
 * powerlevel10k on every prompt redraw, DA, DECRQM — leave false. Mobile needed
 * an `isApplyingEmulatorOutput` discriminator on the send path for this; web
 * must not add one.
 *
 * **The tally counts rows entering the scrollback, never scroll events**
 * (CLA-183). Derived from `BufferService.scroll` at xterm 6.0.0, while unpinned:
 *
 * | what happened                        | what moves                        |
 * |--------------------------------------|-----------------------------------|
 * | row → scrollback, ring not full      | `ybase++`  → `baseY` grows        |
 * | row → scrollback, ring full (trim)   | `ydisp--`  → `viewportY` shrinks  |
 * | DECSTBM region scroll (`scrollTop≠0`)| in-place shift, neither index moves |
 *
 * So the two terms of {@link accrueScrollbackRows} together are exact, and stay
 * exact past the `scrollback` cap — the web analogue of the iOS `yDisp`-freeze
 * that killed three candidate implementations there. It agrees with iOS's
 * `pushesRowIntoScrollback(scrollTop: 0)` on DECSTBM; Android counts those rows
 * because its Termux fork genuinely appends them. Same rule, three-way split in
 * the count, on purpose.
 */

/** Model cap, mirroring Android `MAX_UNREAD_DISPLAY` / iOS `maxUnreadRows`. */
export const MAX_UNREAD_ROWS = 9999;

/** Label saturation, well below the model cap so the badge keeps its size. */
const UNREAD_LABEL_CAP = 999;

/** The two buffer indices the follow math reads. */
export interface ScrollSample {
  baseY: number;
  viewportY: number;
}

export interface FollowState {
  sample: ScrollSample;
  pinned: boolean;
  /** Rows that scrolled past while unpinned. Cleared by re-pinning only. */
  unread: number;
}

export function initialFollowState(): FollowState {
  return { sample: { baseY: 0, viewportY: 0 }, pinned: true, unread: 0 };
}

/**
 * Whether the viewport is parked at the tail.
 *
 * `>=` rather than `===`: xterm clamps `ydisp` to `ybase` so it can never
 * overshoot in steady state, but a reflow mid-resize is not worth a false
 * "scrolled up".
 */
export function isPinned(sample: ScrollSample): boolean {
  return sample.viewportY >= sample.baseY;
}

/** Rows that entered the scrollback between two samples. Never negative. */
export function accrueScrollbackRows(
  previous: ScrollSample,
  next: ScrollSample,
): number {
  return (
    Math.max(0, next.baseY - previous.baseY) +
    Math.max(0, previous.viewportY - next.viewportY)
  );
}

/** Add rows to the tally, saturating at {@link MAX_UNREAD_ROWS}. */
export function addUnread(current: number, rows: number): number {
  if (rows <= 0) return current;
  return Math.min(MAX_UNREAD_ROWS, current + rows);
}

/** Badge text. Saturates at `999+`, matching both mobile clients. */
export function unreadLabel(unread: number): string {
  return unread > UNREAD_LABEL_CAP ? `${String(UNREAD_LABEL_CAP)}+` : String(unread);
}

/**
 * Fold one sample into the follow state.
 *
 * `userDriven` is the re-baseline switch: a wheel, a viewport drag or a paging
 * key moves `viewportY` down exactly like a ring-full trim does, so without it
 * scrolling up would be counted as unread. A resize (reflow moves both indices)
 * and a replay wipe (both reset) re-baseline the same way.
 */
export function foldSample(
  state: FollowState,
  next: ScrollSample,
  userDriven: boolean,
): FollowState {
  const pinned = isPinned(next);
  if (userDriven) {
    return { sample: next, pinned, unread: pinned ? 0 : state.unread };
  }
  const rows = pinned ? 0 : accrueScrollbackRows(state.sample, next);
  return {
    sample: next,
    pinned,
    unread: pinned ? 0 : addUnread(state.unread, rows),
  };
}

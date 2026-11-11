import { describe, expect, it } from 'vitest';
import {
  accrueScrollbackRows,
  addUnread,
  foldSample,
  initialFollowState,
  isPinned,
  MAX_UNREAD_ROWS,
  unreadLabel,
  type FollowState,
  type ScrollSample,
} from './terminalFollow';

/** The two buffer indices, in the order xterm reports them. */
const at = (baseY: number, viewportY: number): ScrollSample => ({ baseY, viewportY });

const unpinnedAt = (sample: ScrollSample, unread: number): FollowState => ({
  sample,
  pinned: false,
  unread,
});

describe('isPinned', () => {
  it('is true only while the viewport sits at the tail', () => {
    expect(isPinned(at(10, 10))).toBe(true);
    expect(isPinned(at(10, 9))).toBe(false);
    expect(isPinned(at(0, 0))).toBe(true);
  });

  it('treats a mid-reflow overshoot as pinned rather than as a scroll-up', () => {
    expect(isPinned(at(10, 12))).toBe(true);
  });
});

describe('accrueScrollbackRows', () => {
  // The three numbers below are the ones xtermBufferContract measures against a
  // real emulator; keeping them here too means a pure-math regression fails
  // without needing the headless build.
  it('counts rows appended while the scrollback ring still has room', () => {
    expect(accrueScrollbackRows(at(2, 0), at(4, 0))).toBe(2);
  });

  it('keeps counting once the ring is full and baseY stops moving', () => {
    // A naive baseY-delta counter reports 0 here and silently stops counting
    // for the rest of the session.
    expect(accrueScrollbackRows(at(10, 5), at(10, 2))).toBe(3);
  });

  it('counts nothing when neither index moved, as in a DECSTBM region scroll', () => {
    expect(accrueScrollbackRows(at(1, 0), at(1, 0))).toBe(0);
  });

  it('floors both terms at zero so a reset cannot produce a negative count', () => {
    expect(accrueScrollbackRows(at(4, 0), at(2, 4))).toBe(0);
  });
});

describe('addUnread', () => {
  it('saturates at the model cap', () => {
    expect(addUnread(MAX_UNREAD_ROWS - 1, 5)).toBe(MAX_UNREAD_ROWS);
    expect(addUnread(0, MAX_UNREAD_ROWS + 10)).toBe(MAX_UNREAD_ROWS);
  });
});

describe('unreadLabel', () => {
  it('saturates at 999+ so the badge keeps its size', () => {
    expect(unreadLabel(0)).toBe('0');
    expect(unreadLabel(999)).toBe('999');
    expect(unreadLabel(1000)).toBe('999+');
    expect(unreadLabel(MAX_UNREAD_ROWS)).toBe('999+');
  });
});

describe('foldSample', () => {
  it('accrues rows that scroll past while the user is scrolled up', () => {
    const scrolledUp = foldSample(initialFollowState(), at(2, 0), true);
    expect(scrolledUp.pinned).toBe(false);
    expect(scrolledUp.unread).toBe(0);

    expect(foldSample(scrolledUp, at(4, 0), false).unread).toBe(2);
  });

  it('keeps accruing past the ring-full boundary', () => {
    expect(foldSample(unpinnedAt(at(10, 5), 1), at(10, 2), false).unread).toBe(4);
  });

  it('clears the tally when the viewport returns to the tail', () => {
    const pinned = foldSample(unpinnedAt(at(10, 2), 42), at(10, 10), false);

    expect(pinned.pinned).toBe(true);
    expect(pinned.unread).toBe(0);
  });

  it('re-baselines without accruing when the user drove the scroll', () => {
    // A wheel moves viewportY down exactly as a ring-full trim does, so the
    // flag is the only thing that can tell them apart. Same input, both ways.
    const state: FollowState = { sample: at(10, 10), pinned: true, unread: 0 };

    expect(foldSample(state, at(10, 4), false).unread).toBe(6);
    expect(foldSample(state, at(10, 4), true).unread).toBe(0);
  });

  it('holds an existing tally across a user scroll that stays unpinned', () => {
    const scrolledFurther = foldSample(unpinnedAt(at(10, 6), 12), at(10, 1), true);

    expect(scrolledFurther.unread).toBe(12);
    expect(scrolledFurther.sample).toEqual(at(10, 1));
  });

  it('does not report a replay wipe as a screenful of unread rows', () => {
    // The wipe resets both indices (baseY 16 -> 0, viewportY 12 -> 0), which a
    // raw accrue reads as 12 rows entering the scrollback.
    const state = unpinnedAt(at(16, 12), 3);
    expect(accrueScrollbackRows(state.sample, at(0, 0))).toBe(12);

    const wiped = foldSample(state, at(0, 0), true);
    expect(wiped.pinned).toBe(true);
    expect(wiped.unread).toBe(0);
  });
});

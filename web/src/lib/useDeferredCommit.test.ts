import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  UNDO_WINDOW_MS,
  useDeferredCommit,
  type DeferredCommitOptions,
} from './useDeferredCommit';

let committed: string[];

const setup = (over: Partial<DeferredCommitOptions<string>> = {}) =>
  renderHook(() =>
    useDeferredCommit<string>({
      onCommit: (payload) => {
        committed.push(payload);
      },
      ...over,
    }),
  );

beforeEach(() => {
  vi.useFakeTimers();
  committed = [];
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('useDeferredCommit', () => {
  it('holds the payload for the whole window before sending it', () => {
    const { result } = setup();

    act(() => {
      result.current.schedule('delete-a');
    });
    expect(result.current.pending).toBe('delete-a');

    act(() => {
      vi.advanceTimersByTime(UNDO_WINDOW_MS - 1);
    });
    expect(committed).toEqual([]);

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(committed).toEqual(['delete-a']);
    expect(result.current.pending).toBeNull();
  });

  it('sends immediately when the bar is dismissed, and only once', () => {
    const { result } = setup();

    act(() => {
      result.current.schedule('delete-a');
    });
    act(() => {
      result.current.commitNow();
    });

    expect(committed).toEqual(['delete-a']);
    expect(result.current.pending).toBeNull();

    act(() => {
      vi.advanceTimersByTime(UNDO_WINDOW_MS);
    });
    expect(committed).toEqual(['delete-a']);
  });

  it('sends nothing at all after an undo', () => {
    const { result } = setup();

    act(() => {
      result.current.schedule('delete-a');
    });
    act(() => {
      result.current.cancel();
    });

    expect(result.current.pending).toBeNull();
    act(() => {
      vi.advanceTimersByTime(UNDO_WINDOW_MS);
    });
    expect(committed).toEqual([]);
  });

  it('commits the earlier payload rather than dropping it when no merge is given', () => {
    const { result } = setup();

    act(() => {
      result.current.schedule('delete-a');
    });
    act(() => {
      result.current.schedule('delete-b');
    });

    expect(committed).toEqual(['delete-a']);
    expect(result.current.pending).toBe('delete-b');

    act(() => {
      vi.advanceTimersByTime(UNDO_WINDOW_MS);
    });
    expect(committed).toEqual(['delete-a', 'delete-b']);
  });

  it('restarts the window around a coalesced batch without stranding the first payload', () => {
    const { result } = setup({
      merge: (previous, next) => ({ commit: null, next: `${previous}+${next}` }),
    });

    act(() => {
      result.current.schedule('a');
    });
    act(() => {
      vi.advanceTimersByTime(UNDO_WINDOW_MS - 1_000);
    });
    act(() => {
      result.current.schedule('b');
    });

    // The first window would have elapsed by now; the restart replaced it.
    act(() => {
      vi.advanceTimersByTime(UNDO_WINDOW_MS - 1_000);
    });
    expect(committed).toEqual([]);
    expect(result.current.pending).toBe('a+b');

    act(() => {
      vi.advanceTimersByTime(1_000);
    });
    expect(committed).toEqual(['a+b']);
  });

  it('cancels rather than commits when the screen goes away mid-window', () => {
    const { result, unmount } = setup();

    act(() => {
      result.current.schedule('delete-a');
    });
    unmount();

    act(() => {
      vi.advanceTimersByTime(UNDO_WINDOW_MS);
    });
    expect(committed).toEqual([]);
  });
});

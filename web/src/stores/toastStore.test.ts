import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useToastStore } from './toastStore';

describe('toastStore', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useToastStore.setState({ toasts: [] });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('dismiss removes only the targeted toast', () => {
    const first = useToastStore.getState().show('first');
    const second = useToastStore.getState().show('second');

    useToastStore.getState().dismiss(first);

    const { toasts } = useToastStore.getState();
    expect(toasts).toHaveLength(1);
    expect(toasts[0]?.id).toBe(second);
  });

  it('auto-dismisses after the default 5s', () => {
    useToastStore.getState().show('Ephemeral');

    vi.advanceTimersByTime(4999);
    expect(useToastStore.getState().toasts).toHaveLength(1);

    vi.advanceTimersByTime(1);
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });

  it('does not auto-dismiss when durationMs is 0', () => {
    useToastStore.getState().show('Sticky', 'error', 0);

    vi.advanceTimersByTime(60_000);
    expect(useToastStore.getState().toasts).toHaveLength(1);
  });
});

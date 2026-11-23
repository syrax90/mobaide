import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../api/errors';
import { useToastStore } from '../../stores/toastStore';
import { scanFailureHandler } from './autoscanMutations';

function apiError(status: number): ApiError {
  return new ApiError({
    status,
    message: 'refused',
    code: null,
    retryAfterSeconds: null,
    detail: null,
  });
}

describe('scanFailureHandler', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useToastStore.setState({ toasts: [] });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it.each([
    // The PC no longer runs the scan: this used to be swallowed without a word.
    [404, true],
    // The relay's answer for an unreachable agent: the scan may still run.
    [503, false],
  ])('a %i shows an error, and marks the scan gone: %s', (status, gone) => {
    let markedGone = false;

    scanFailureHandler('stop', () => Promise.resolve(), () => {
      markedGone = true;
    })(apiError(status));

    expect(markedGone).toBe(gone);
    expect(useToastStore.getState().toasts).toMatchObject([{ kind: 'error' }]);
  });
});

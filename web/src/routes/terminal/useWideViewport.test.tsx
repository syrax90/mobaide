import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { stubMediaQuery } from '../../test/matchMediaStub';
import { WIDE_VIEWPORT_QUERY, useWideViewport } from './useWideViewport';


afterEach(() => {
  cleanup();
  // Back to narrow, as `setup.ts` answers; never `vi.unstubAllGlobals`, which
  // would take other suites' stand-ins with it.
  stubMediaQuery(WIDE_VIEWPORT_QUERY, false);
});

describe('useWideViewport', () => {

  it('follows the window across the breakpoint', () => {
    // The pin only docks on a wide screen; a window dragged narrower has to
    // hand the panel back to the overlay without a reload.
    const media = stubMediaQuery(WIDE_VIEWPORT_QUERY, true);
    const { result } = renderHook(() => useWideViewport());

    act(() => {
      media.set(false);
    });
    expect(result.current).toBe(false);

    act(() => {
      media.set(true);
    });
    expect(result.current).toBe(true);
  });
});

import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { useFullscreen } from './useFullscreen';

let calls: string[];
let inFullscreen: boolean;
let requestResult: () => Promise<void>;

/** jsdom implements none of the Fullscreen API; the whole surface is a stub. */
function stubFullscreenApi({ enabled = true, request = true } = {}): void {
  Object.defineProperty(document, 'fullscreenEnabled', { configurable: true, get: () => enabled });
  if (request) {
    Object.defineProperty(document.documentElement, 'requestFullscreen', {
      configurable: true,
      value: (): Promise<void> => {
        calls.push('requestFullscreen');
        return requestResult();
      },
    });
  }
  Object.defineProperty(document, 'exitFullscreen', {
    configurable: true,
    value: (): Promise<void> => {
      calls.push('exitFullscreen');
      return Promise.resolve();
    },
  });
  Object.defineProperty(document, 'fullscreenElement', {
    configurable: true,
    get: () => (inFullscreen ? document.documentElement : null),
  });
}

function browserSwitches(on: boolean): void {
  inFullscreen = on;
  act(() => {
    document.dispatchEvent(new Event('fullscreenchange'));
  });
}

beforeEach(() => {
  calls = [];
  inFullscreen = false;
  requestResult = () => Promise.resolve();
});

afterEach(() => {
  cleanup();
  Reflect.deleteProperty(document, 'fullscreenEnabled');
  Reflect.deleteProperty(document.documentElement, 'requestFullscreen');
  Reflect.deleteProperty(document, 'exitFullscreen');
  Reflect.deleteProperty(document, 'fullscreenElement');
});

describe('useFullscreen', () => {
  it('is not offered where the page cannot go full screen', () => {
    stubFullscreenApi({ enabled: false });
    expect(renderHook(useFullscreen).result.current.supported).toBe(false);
    cleanup();
    Reflect.deleteProperty(document.documentElement, 'requestFullscreen');

    stubFullscreenApi({ request: false });
    expect(renderHook(useFullscreen).result.current.supported).toBe(false);
  });

  it('starts from the document state and follows the browser', () => {
    stubFullscreenApi();
    inFullscreen = true;
    const { result } = renderHook(useFullscreen);
    expect(result.current.supported).toBe(true);
    expect(result.current.active).toBe(true);

    browserSwitches(false);
    expect(result.current.active).toBe(false);

    browserSwitches(true);
    expect(result.current.active).toBe(true);
  });

  it('reports a refused request and clears the report on the next try', async () => {
    stubFullscreenApi();
    requestResult = () => Promise.reject(new Error('not allowed'));
    const { result } = renderHook(useFullscreen);

    await act(async () => {
      result.current.enter();
      await Promise.resolve();
    });
    expect(calls).toEqual(['requestFullscreen']);
    expect(result.current.error).toBe('Your browser did not allow full screen.');

    requestResult = () => Promise.resolve();
    await act(async () => {
      result.current.enter();
      await Promise.resolve();
    });
    expect(result.current.error).toBeNull();
  });

  it('leaves full screen only when the page is in it', () => {
    stubFullscreenApi();
    const { result } = renderHook(useFullscreen);

    act(() => {
      result.current.exit();
    });
    expect(calls).toEqual([]);

    browserSwitches(true);
    act(() => {
      result.current.exit();
    });
    expect(calls).toEqual(['exitFullscreen']);
  });
});

import { afterEach, describe, expect, it, vi } from 'vitest';
import { detectBrowser, detectOs, isStandaloneDisplay } from './browserEnv';

/** Real user-agent strings: the sniffing order only matters against real ones. */
const UA = {
  chromeMac:
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  chromeWindows:
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  edgeWindows:
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.2903.86',
  firefoxWindows:
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0',
  firefoxLinux: 'Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0',
  safariMac:
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15',
  firefoxIos:
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) FxiOS/133.0 Mobile/15E148 Safari/605.1.15',
  chromeAndroid:
    'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36',
  safariIphone:
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
  chromeOs:
    'Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
};

describe('detectBrowser', () => {
  // The whole reason the checks are ordered: every Chromium UA ends in
  // "Safari", and Edge's carries "Chrome" *and* "Safari" too. A naive
  // most-general-first table answers 'safari' for all three.
  it('reads Chromium out of a UA that also claims Safari', () => {
    expect(detectBrowser(UA.chromeMac)).toBe('chromium');
    expect(detectBrowser(UA.chromeWindows)).toBe('chromium');
  });

  it('reads Edge as Chromium rather than as Chrome-then-Safari', () => {
    expect(detectBrowser(UA.edgeWindows)).toBe('chromium');
  });

  it('reads Firefox on iOS as Firefox even though its UA says Safari', () => {
    expect(detectBrowser(UA.firefoxIos)).toBe('firefox');
  });

  it('reads real Safari as Safari', () => {
    expect(detectBrowser(UA.safariMac)).toBe('safari');
  });
});

describe('detectOs', () => {
  it('names the three desktop platforms the reserved table is indexed on', () => {
    expect(detectOs(UA.chromeMac, 'MacIntel')).toBe('mac');
    expect(detectOs(UA.firefoxWindows, 'Win32')).toBe('windows');
    expect(detectOs(UA.firefoxLinux, 'Linux x86_64')).toBe('linux');
  });

  it('treats Chrome OS as Linux', () => {
    expect(detectOs(UA.chromeOs, 'Linux x86_64')).toBe('linux');
  });

  // Android's UA says "Linux" and iOS's says "like Mac OS X", so without the
  // up-front exclusion both would be classified as desktop platforms and
  // inherit a reserved-key table written for window managers they do not have.
  it('excludes Android despite its Linux UA', () => {
    expect(detectOs(UA.chromeAndroid, 'Linux armv8l')).toBe('other');
  });

  it('excludes iOS despite its Mac-shaped UA', () => {
    expect(detectOs(UA.safariIphone, 'iPhone')).toBe('other');
    expect(detectOs(UA.firefoxIos, 'iPhone')).toBe('other');
  });

  it('reads the platform string, not only the UA', () => {
    expect(detectOs('SomeBot/1.0', 'iPad')).toBe('other');
    expect(detectOs('SomeBot/1.0', 'Win32')).toBe('windows');
  });
});

describe('isStandaloneDisplay', () => {
  // jsdom ships no `matchMedia` at all, which is also the shape the guard in
  // `isStandaloneDisplay` exists for — so it is stubbed onto the global rather
  // than spied on.
  function stubDisplayMode(...standing: string[]): void {
    vi.stubGlobal(
      'matchMedia',
      (query: string) => ({ matches: standing.includes(query) }) as MediaQueryList,
    );
  }

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('is false in a browser with no display-mode query, rather than throwing', () => {
    // The bar reads this on every render; an older engine without `matchMedia`
    // has to yield "not installed", not a TypeError up through the tree.
    vi.stubGlobal('matchMedia', undefined);

    expect(isStandaloneDisplay()).toBe(false);
  });

  it('is true in an installed standalone window', () => {
    stubDisplayMode('(display-mode: standalone)');

    expect(isStandaloneDisplay()).toBe(true);
  });

  // A desktop PWA with a custom title bar reports `window-controls-overlay`
  // and never `standalone`, so checking only the latter would leave the
  // keyboard widening off in exactly the install shape that most needs it.
  it('is true under a window-controls-overlay title bar', () => {
    stubDisplayMode('(display-mode: window-controls-overlay)');

    expect(isStandaloneDisplay()).toBe(true);
  });
});

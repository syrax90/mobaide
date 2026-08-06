/**
 * Which browser, OS and display mode the page is running in.
 *
 * This exists for one reason: which keyboard chords the browser or the window
 * manager takes before the page ever sees them is a per-browser, per-OS,
 * per-display-mode fact, and `reservedKeys.ts` is indexed on it. Nothing else
 * should sniff the user agent — feature-detect instead.
 *
 * Deliberately not merged with `detectIsMac` in `terminal/terminalKeys.ts`: that
 * one answers "does this platform use Cmd for clipboard chords" and returns true
 * for iPhone and iPad, which are not desktop keyboard environments at all.
 */

export type BrowserName = 'chromium' | 'firefox' | 'safari' | 'unknown';
export type OsName = 'mac' | 'windows' | 'linux' | 'other';

export interface KeyboardEnv {
  browser: BrowserName;
  os: OsName;
  /** Running as an installed app rather than in a browser tab. */
  standalone: boolean;
}

/**
 * Order matters. Every Chromium UA also contains "Safari", and Edge's contains
 * both "Chrome" and "Safari", so the specific tokens have to be tested first.
 */
export function detectBrowser(userAgent: string): BrowserName {
  if (/firefox|fxios/i.test(userAgent)) return 'firefox';
  if (/edg\/|edgios|opr\/|chromium|crios|chrome/i.test(userAgent)) return 'chromium';
  if (/safari/i.test(userAgent)) return 'safari';
  return 'unknown';
}

export function detectOs(userAgent: string, platform: string): OsName {
  const haystack = `${userAgent} ${platform}`;
  // Both report a Linux- or Mac-shaped UA, so they have to be excluded first.
  // Neither has a hardware keyboard whose chords a window manager competes for.
  if (/android|iphone|ipad|ipod/i.test(haystack)) return 'other';
  if (/mac/i.test(haystack)) return 'mac';
  if (/win/i.test(haystack)) return 'windows';
  if (/linux|x11|cros/i.test(haystack)) return 'linux';
  return 'other';
}

/**
 * True in an installed PWA window. The Tier-1 keyboard widening reads this; see
 * `relaxedInStandalone` in `reservedKeys.ts` for why its effect is still
 * conservative.
 */
export function isStandaloneDisplay(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return false;
  }
  return (
    window.matchMedia('(display-mode: standalone)').matches ||
    window.matchMedia('(display-mode: window-controls-overlay)').matches
  );
}

export function currentKeyboardEnv(): KeyboardEnv {
  if (typeof navigator === 'undefined') {
    return { browser: 'unknown', os: 'other', standalone: false };
  }
  return {
    browser: detectBrowser(navigator.userAgent),
    os: detectOs(navigator.userAgent, navigator.platform),
    standalone: isStandaloneDisplay(),
  };
}

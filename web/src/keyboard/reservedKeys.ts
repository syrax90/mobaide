/**
 * Chords the browser or the window manager claims before the terminal can have
 * them, indexed by browser, OS and display mode.
 *
 * Two independent facts per row, deliberately kept apart:
 *
 * - `browserAction` — what the chord does by default. Always knowable from
 *   vendor documentation, which is what `source` cites.
 * - `pageCanCancel` — whether `preventDefault()` on the page stops it. This is
 *   *not* documented anywhere for most chords, so it starts at `'unknown'` and
 *   only becomes `false`/`true` when a vendor has said so or someone measured
 *   it. Firefox's Ctrl+W/T/N are the one large `false` group with a citation.
 *
 * Conflating them is the mistake this table exists to avoid: Firefox delivers
 * Ctrl+W to the page *and* closes the tab anyway, so "the page sees it" proves
 * nothing about whether the terminal gets to keep it.
 *
 * Every row ships at `confidence: 'documented'`. Promoting rows to `'measured'`
 * with the DEV-only `/dev/key-sweep` harness is CLA-251; the runbook is
 * `docs/web-keyboard-reserved-keys.md`.
 */
import type { BrowserName, KeyboardEnv, OsName } from './browserEnv';

export interface ReservedKey {
  /** A chord token from the shared grammar (`protocol/keyChord.ts`). */
  token: string;
  browsers: readonly BrowserName[];
  platforms: readonly OsName[];
  browserAction: string;
  pageCanCancel: boolean | 'unknown';
  /**
   * Whether an installed PWA window is believed to release this chord back to
   * the page. Anecdotal and unverified for every row that carries it — the
   * macOS Cmd+W probe in CLA-251 is what settles it — so it only softens the
   * warning, it never removes it.
   */
  relaxedInStandalone?: boolean;
  /**
   * The app suppresses this chord itself while a terminal is attached
   * (`useReloadGuard`), so it *does* reach the PTY. Kept in the table rather
   * than deleted so the composer can say so instead of staying silent — a user
   * saving Ctrl+R (reverse-i-search) deserves the reassurance, not a warning
   * about a reload that will not happen.
   */
  appSuppressed?: boolean;
  source: string;
  confidence: 'documented' | 'measured';
}

const CHROMIUM_SHORTCUTS = 'https://support.google.com/chrome/answer/157179';
const FIREFOX_SHORTCUTS =
  'https://support.mozilla.org/en-US/kb/keyboard-shortcuts-perform-firefox-tasks-quickly';
const FIREFOX_WONTFIX = 'https://bugzilla.mozilla.org/show_bug.cgi?id=1291706';
const SAFARI_SHORTCUTS =
  'https://support.apple.com/guide/safari/keyboard-and-other-shortcuts-cpsh003/mac';
const MACOS_FUNCTION_KEYS =
  'https://support.apple.com/guide/mac-help/use-keyboard-function-keys-mchlp2596/mac';
const WINDOWS_SHORTCUTS =
  'https://support.microsoft.com/en-us/windows/keyboard-shortcuts-in-windows-dcc61a57-8ff0-cffe-9796-cb9706c75eec';

const DESKTOP: readonly OsName[] = ['mac', 'windows', 'linux'];
/** macOS puts these on Cmd, so the Ctrl form is free there. */
const PC: readonly OsName[] = ['windows', 'linux'];
const BROWSERS: readonly BrowserName[] = ['chromium', 'firefox', 'safari'];
const CHROMIUM: readonly BrowserName[] = ['chromium'];
const FIREFOX: readonly BrowserName[] = ['firefox'];
const CHROMIUM_AND_FIREFOX: readonly BrowserName[] = ['chromium', 'firefox'];

function row(
  token: string,
  browsers: readonly BrowserName[],
  platforms: readonly OsName[],
  browserAction: string,
  pageCanCancel: boolean | 'unknown',
  source: string,
  relaxedInStandalone?: boolean,
): ReservedKey {
  return {
    token,
    browsers,
    platforms,
    browserAction,
    pageCanCancel,
    ...(relaxedInStandalone === undefined ? {} : { relaxedInStandalone }),
    source,
    confidence: 'documented',
  };
}

/**
 * The one group with a vendor statement behind `pageCanCancel: false`: Firefox
 * stopped letting content override tab and window management in Firefox 48 and
 * closed the request WONTFIX — "we are not going to allow content authors to
 * block users from using their browser as they see fit".
 */
const FIREFOX_UNSUPPRESSABLE: readonly ReservedKey[] = [
  row('ctrl+w', FIREFOX, PC, 'Close tab', false, FIREFOX_WONTFIX),
  row('ctrl+t', FIREFOX, PC, 'New tab', false, FIREFOX_WONTFIX),
  row('ctrl+n', FIREFOX, PC, 'New window', false, FIREFOX_WONTFIX),
];

const TAB_AND_WINDOW_MANAGEMENT: readonly ReservedKey[] = [
  row('ctrl+w', CHROMIUM, PC, 'Close tab', 'unknown', CHROMIUM_SHORTCUTS, true),
  row('ctrl+t', CHROMIUM, PC, 'New tab', 'unknown', CHROMIUM_SHORTCUTS, true),
  row('ctrl+n', CHROMIUM, PC, 'New window', 'unknown', CHROMIUM_SHORTCUTS, true),
  row('ctrl+shift+w', CHROMIUM, PC, 'Close window', 'unknown', CHROMIUM_SHORTCUTS, true),
  row('ctrl+shift+t', CHROMIUM, PC, 'Reopen closed tab', 'unknown', CHROMIUM_SHORTCUTS, true),
  row('ctrl+shift+n', CHROMIUM, PC, 'New incognito window', 'unknown', CHROMIUM_SHORTCUTS),
  row('ctrl+shift+p', FIREFOX, PC, 'New private window', 'unknown', FIREFOX_SHORTCUTS),
  row('ctrl+shift+t', FIREFOX, PC, 'Reopen closed tab', 'unknown', FIREFOX_SHORTCUTS, true),
  // Ctrl+Tab cycles tabs on every desktop platform, macOS included — it is one
  // of the few browser chords Apple did not move to Cmd.
  row('ctrl+tab', BROWSERS, DESKTOP, 'Next tab', 'unknown', CHROMIUM_SHORTCUTS, true),
  row('ctrl+shift+tab', BROWSERS, DESKTOP, 'Previous tab', 'unknown', CHROMIUM_SHORTCUTS, true),
  ...Array.from({ length: 9 }, (_, i) =>
    row(
      `ctrl+${String(i + 1)}`,
      CHROMIUM_AND_FIREFOX,
      PC,
      i === 8 ? 'Switch to the last tab' : `Switch to tab ${String(i + 1)}`,
      'unknown',
      CHROMIUM_SHORTCUTS,
      true,
    ),
  ),
];

/**
 * Ctrl+L and Ctrl+O matter more than the rest of this group: both are built-in
 * control-bar keys (clear screen, transcript toggle), so on Windows and Linux
 * the bar button may be the only way to deliver them.
 */
const BROWSER_UI: readonly ReservedKey[] = [
  row('ctrl+l', CHROMIUM_AND_FIREFOX, PC, 'Focus the address bar', 'unknown', CHROMIUM_SHORTCUTS),
  row('alt+d', CHROMIUM_AND_FIREFOX, PC, 'Focus the address bar', 'unknown', CHROMIUM_SHORTCUTS),
  row('ctrl+o', CHROMIUM_AND_FIREFOX, PC, 'Open a file', 'unknown', CHROMIUM_SHORTCUTS),
  row('ctrl+p', CHROMIUM_AND_FIREFOX, PC, 'Print', 'unknown', CHROMIUM_SHORTCUTS),
  row('ctrl+s', CHROMIUM_AND_FIREFOX, PC, 'Save the page', 'unknown', CHROMIUM_SHORTCUTS),
  row('ctrl+f', CHROMIUM_AND_FIREFOX, PC, 'Find in page', 'unknown', CHROMIUM_SHORTCUTS),
  row('ctrl+g', CHROMIUM_AND_FIREFOX, PC, 'Find next', 'unknown', CHROMIUM_SHORTCUTS),
  row('ctrl+d', CHROMIUM_AND_FIREFOX, PC, 'Bookmark the page', 'unknown', CHROMIUM_SHORTCUTS),
  row('ctrl+h', CHROMIUM_AND_FIREFOX, PC, 'Open history', 'unknown', CHROMIUM_SHORTCUTS),
  // Also 0x0A — a chord a shell user may genuinely want.
  row('ctrl+j', CHROMIUM_AND_FIREFOX, PC, 'Open downloads', 'unknown', CHROMIUM_SHORTCUTS),
  row('ctrl+u', CHROMIUM_AND_FIREFOX, PC, 'View page source', 'unknown', CHROMIUM_SHORTCUTS),
];

/**
 * Chords whose default action `useReloadGuard` cancels for us. `pageCanCancel`
 * stays `'unknown'` — no vendor has documented it and nobody has measured it;
 * what we know is only that *our* guard runs, which is what `appSuppressed`
 * records. Keeping the distinction is the whole point of the two-field split at
 * the top of this file.
 */
const APP_SUPPRESSED: readonly ReservedKey[] = [
  {
    ...row('ctrl+r', BROWSERS, DESKTOP, 'Reload', 'unknown', CHROMIUM_SHORTCUTS),
    appSuppressed: true,
  },
  {
    ...row('ctrl+shift+r', BROWSERS, DESKTOP, 'Hard reload', 'unknown', CHROMIUM_SHORTCUTS),
    appSuppressed: true,
  },
  {
    ...row('f5', CHROMIUM_AND_FIREFOX, PC, 'Reload', 'unknown', CHROMIUM_SHORTCUTS),
    appSuppressed: true,
  },
];

const DEVTOOLS: readonly ReservedKey[] = [
  row('ctrl+shift+i', CHROMIUM, PC, 'Open DevTools', 'unknown', CHROMIUM_SHORTCUTS),
  row('ctrl+shift+j', CHROMIUM, PC, 'Open the DevTools console', 'unknown', CHROMIUM_SHORTCUTS),
  row('ctrl+shift+c', CHROMIUM, PC, 'Open the DevTools element picker', 'unknown', CHROMIUM_SHORTCUTS),
  row('ctrl+shift+i', FIREFOX, PC, 'Open the Inspector', 'unknown', FIREFOX_SHORTCUTS),
  row('ctrl+shift+k', FIREFOX, PC, 'Open the Web Console', 'unknown', FIREFOX_SHORTCUTS),
  row('ctrl+shift+c', FIREFOX, PC, 'Open the element picker', 'unknown', FIREFOX_SHORTCUTS),
];

const FUNCTION_KEYS: readonly ReservedKey[] = [
  row('f1', CHROMIUM, PC, 'Open Chrome help', 'unknown', CHROMIUM_SHORTCUTS),
  row('f3', CHROMIUM_AND_FIREFOX, PC, 'Find next', 'unknown', CHROMIUM_SHORTCUTS),
  row('f6', CHROMIUM_AND_FIREFOX, PC, 'Move focus to the browser toolbar', 'unknown', CHROMIUM_SHORTCUTS),
  row('f7', FIREFOX, PC, 'Toggle caret browsing', 'unknown', FIREFOX_SHORTCUTS),
  row('f11', CHROMIUM_AND_FIREFOX, PC, 'Toggle browser fullscreen', 'unknown', CHROMIUM_SHORTCUTS),
  row('f12', CHROMIUM_AND_FIREFOX, PC, 'Open DevTools', 'unknown', CHROMIUM_SHORTCUTS),
  // macOS is the odd one out: the page never sees a bare F-key unless the user
  // has turned on "Use F1, F2, etc. keys as standard function keys", because the
  // hardware key is a brightness/volume control first.
  ...Array.from({ length: 12 }, (_, i) =>
    row(
      `f${String(i + 1)}`,
      BROWSERS,
      ['mac'],
      'macOS media or brightness control, unless "Use F1, F2, etc. keys as standard function keys" is enabled',
      false,
      MACOS_FUNCTION_KEYS,
    ),
  ),
];

/** Window-manager chords: the browser never sees these either. */
const WINDOW_MANAGER: readonly ReservedKey[] = [
  row('alt+f4', BROWSERS, ['windows'], 'Close the active window', false, WINDOWS_SHORTCUTS),
  row('alt+tab', BROWSERS, ['windows', 'linux'], 'Switch windows', false, WINDOWS_SHORTCUTS),
  row('alt+space', BROWSERS, ['windows'], 'Open the window system menu', 'unknown', WINDOWS_SHORTCUTS),
];

/**
 * Safari's own shortcuts are almost entirely Cmd-based, so very little of the
 * Ctrl/Alt/Shift chord domain is reserved there. That is a claim from Apple's
 * shortcut list rather than a measurement, and it is exactly what the sweep in
 * CLA-251 exists to confirm — an empty Safari section is a hypothesis.
 */
const SAFARI: readonly ReservedKey[] = [
  row('ctrl+f2', BROWSERS, ['mac'], 'Move focus to the menu bar (macOS)', 'unknown', SAFARI_SHORTCUTS),
];

export const RESERVED_KEYS: readonly ReservedKey[] = [
  ...FIREFOX_UNSUPPRESSABLE,
  ...TAB_AND_WINDOW_MANAGEMENT,
  ...BROWSER_UI,
  ...APP_SUPPRESSED,
  ...DEVTOOLS,
  ...FUNCTION_KEYS,
  ...WINDOW_MANAGER,
  ...SAFARI,
];

export interface ReservedLookup {
  entries: readonly ReservedKey[];
  /** True when at least one matching entry is known to be unstoppable. */
  blocked: boolean;
  /** Softened because the page is running as an installed app. */
  relaxed: boolean;
  /** Every match is a chord the app cancels itself, so it reaches the PTY. */
  appSuppressed: boolean;
}

/**
 * Every entry that applies to `token` in `env`, plus the two derived flags the
 * recorder's warning text switches on.
 */
export function lookupReservedKey(token: string, env: KeyboardEnv): ReservedLookup {
  const matching = RESERVED_KEYS.filter(
    (entry) =>
      entry.token === token &&
      entry.browsers.includes(env.browser) &&
      entry.platforms.includes(env.os),
  );
  if (matching.length === 0) {
    return { entries: [], blocked: false, relaxed: false, appSuppressed: false };
  }
  const relaxable =
    env.standalone && matching.every((entry) => entry.relaxedInStandalone === true);
  return {
    entries: matching,
    // Every match, not some: a chord the app cancels in one browser but that a
    // window manager also claims is still lost.
    appSuppressed: matching.every((entry) => entry.appSuppressed === true),
    // A relaxed lookup still reports its entries so the recorder can warn; it
    // just stops calling the chord unusable, because an installed window may
    // hand it back. It never relaxes a row without `relaxedInStandalone`.
    blocked: !relaxable && matching.some((entry) => entry.pageCanCancel === false),
    relaxed: relaxable,
  };
}

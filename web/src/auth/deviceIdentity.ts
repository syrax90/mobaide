import { jwtClaims } from './jwtClaims';

const DEVICE_ID_KEY = 'mobaide-device-id';

/**
 * The `platform` this client reports when registering a device. Surfaces on
 * the devices screen next to the mobile clients' `"ios"` / `"android"`.
 */
export const DEVICE_PLATFORM = 'web';

const UUID_PATTERN =
  /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;

/**
 * `crypto.randomUUID()` exists only in a secure context, which covers the
 * production origin and `localhost` but not a LAN dev host reached over plain
 * http. `crypto.getRandomValues` has no such restriction, so fall back to
 * assembling a v4 UUID by hand rather than degrading to `Math.random()`.
 */
function randomUuid(): string {
  if (typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40;
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20),
  ].join('-');
}

let cached: string | null = null;

/**
 * The stable per-browser-profile device id, minted once and persisted.
 *
 * Deliberately `localStorage`, not `sessionStorage`: this outlives sign-out on
 * every platform (Android keeps it in a dedicated DataStore, iOS in the
 * keychain) so the same browser keeps one row on the devices screen instead of
 * accumulating one per session.
 *
 * Never seed this from another client's id. `upsert_mobile_device` is
 * last-login-wins, so reusing a phone's device id would revoke that phone's
 * refresh tokens. The server also rejects anything that is not a canonical
 * 36-char UUID (`MOBILE_DEVICE_ID_PATTERN`), which is why a stored value that
 * fails the shape check is replaced rather than sent.
 */
export function getDeviceId(): string {
  if (cached !== null) {
    return cached;
  }
  let stored: string | null = null;
  try {
    stored = localStorage.getItem(DEVICE_ID_KEY);
  } catch {
    stored = null;
  }
  const id = stored !== null && UUID_PATTERN.test(stored) ? stored : randomUuid();
  if (id !== stored) {
    try {
      localStorage.setItem(DEVICE_ID_KEY, id);
    } catch {
      // Storage denied (private mode, quota). The id still works for this
      // page's lifetime; the next load registers a new device rather than
      // failing sign-in.
    }
  }
  cached = id;
  return id;
}

/**
 * Make the device id the one the session in `accessToken` is bound to.
 *
 * The server binds a session to the `device_id` sent at sign-in and refuses
 * agent tokens for any other id. Safari's ITP clears `localStorage` after
 * seven days without a visit but keeps the 30-day refresh cookie, so a
 * restored session can outlive the stored id; adopting its claim keeps the
 * two equal. A token without the claim (an unbound session) changes nothing.
 */
export function adoptSessionDeviceId(accessToken: string): void {
  const claimed = jwtClaims(accessToken)?.device_id;
  if (typeof claimed !== 'string' || !UUID_PATTERN.test(claimed)) return;
  if (claimed === cached) return;
  cached = claimed;
  try {
    localStorage.setItem(DEVICE_ID_KEY, claimed);
  } catch {
    // Storage denied: the in-process cache still serves this page.
  }
}

/** Test seam — drops the in-process cache so a fresh id is read from storage. */
export function resetDeviceIdCache(): void {
  cached = null;
}

/**
 * Label shown on the devices screen, matching the mobile clients' pattern of a
 * vendor-ish prefix plus a model (`"Google Pixel 8"`, `"Apple iPhone17,2"`).
 * `navigator.userAgent` is the only browser/OS hint available without the
 * unshipped-everywhere UA-Client-Hints API, and it is only ever a display
 * label — nothing authenticates on it.
 */
export function getDeviceName(): string {
  const ua = navigator.userAgent;
  const browser =
    /Firefox\/[\d.]+/.exec(ua)?.[0].split('/')[0] ??
    (/Edg\//.test(ua)
      ? 'Edge'
      : /OPR\//.test(ua)
        ? 'Opera'
        : /Chrome\//.test(ua)
          ? 'Chrome'
          : /Safari\//.test(ua)
            ? 'Safari'
            : null);
  // Order matters: every iPhone/iPad user agent contains the string
  // "like Mac OS X", so a generic /Mac OS X/ test placed first would claim
  // every iPhone is a Mac. Android likewise contains "Linux". Test the most
  // specific markers first.
  //
  // Not fixable here: iPadOS in desktop mode reports "Macintosh; Intel Mac OS
  // X" with no iPad token at all, so it registers as macOS.
  const os = /(iPhone|iPad|iPod)/.test(ua)
    ? 'iOS'
    : /Android/.test(ua)
      ? 'Android'
      : /Windows/.test(ua)
        ? 'Windows'
        : /Mac OS X/.test(ua)
          ? 'macOS'
          : /Linux/.test(ua)
            ? 'Linux'
            : null;
  if (browser !== null && os !== null) return `${browser} on ${os}`;
  if (browser !== null) return browser;
  return 'Browser';
}

/**
 * The `MOBA1|…` pairing payload: the fourth implementation of a contract
 * Python, Kotlin and Swift already carry.
 *
 * Wire format (`docs/qr-pairing.md`):
 *
 * ```
 * MOBA1|{relay_url}|{nonce}|{trust_key_fp}|{exp_unix}
 * ```
 *
 * Pinned across all four by `tests/protocol_fixtures/pairing/qr_payload.json`.
 * Ports `mobaide_agent/mobaide/agent/pairing_qr.parse_qr_payload`,
 * `QrPairingScanner.parseMoba1` (Kotlin) and `PairingPayload.parseMoba1`
 * (Swift). Framework-free on purpose — no React, no TanStack — so the golden
 * can drive it from a Node environment.
 */

/** `^MOBA1|` on the whitespace-stripped text — the anchored paste-box guard. */
const PAYLOAD_PREFIX = 'MOBA1|';

const VERSION = 'MOBA1';

/**
 * What every client's integer parse accepts, spelled out.
 *
 * `Number()` alone is far looser than Kotlin's `toLongOrNull` and Swift's
 * `Int64(_:)`: it takes `0x10`, `1e9`, `Infinity`, an empty string (→ 0) and
 * surrounding whitespace. Each of those would parse here and be refused on a
 * phone.
 */
const EXP_UNIX_PATTERN = /^[+-]?[0-9]+$/;

const BASE64URL_PATTERN = /^[A-Za-z0-9_-]+={0,2}$/;

export interface ParsedPairingPayload {
  version: string;
  relayUrl: string;
  nonce: string;
  trustKeyFp: string;
  expUnix: number;
}

function isBlank(value: string): boolean {
  return value.trim() === '';
}

/**
 * Parse a payload, or `null` on any malformedness.
 *
 * Deliberately undifferentiated: both mobile clients return one nullable value
 * and surface a single "not a Mobaide pairing code" message, so a caller
 * cannot accidentally tell a scanner-error apart from a wrong-version one and
 * leak the distinction into copy the other platforms do not have.
 *
 * Expiry is **not** range-checked against the clock — a negative `expUnix`
 * parses, and every client pins that. The staleness decision belongs to the
 * pairing state machine, which needs the parsed value to say "expired N
 * minutes ago" rather than "malformed".
 *
 * One web-only tightening: an `expUnix` outside IEEE-754 safe-integer range is
 * rejected rather than silently rounded. The 64-bit clients accept up to
 * 2^63-1; the gap between 2^53 and 2^63 is 285 million years of Unix time, so
 * nothing real lives there, and rounding would hand the expiry check a
 * different number than the one on the wire.
 */
export function parseMoba1(raw: string): ParsedPairingPayload | null {
  const parts = raw.split('|');
  if (parts.length !== 5) return null;
  const [version, relayUrl, nonce, trustKeyFp, expStr] = parts as [
    string,
    string,
    string,
    string,
    string,
  ];
  if (version !== VERSION) return null;
  // Blankness is tested after trimming, matching Kotlin `isBlank()` and
  // Swift `trimmingCharacters`, but the values are stored verbatim.
  if (isBlank(relayUrl) || isBlank(nonce) || isBlank(trustKeyFp)) return null;
  if (!EXP_UNIX_PATTERN.test(expStr)) return null;
  const expUnix = Number(expStr);
  if (!Number.isSafeInteger(expUnix)) return null;
  return { version, relayUrl, nonce, trustKeyFp, expUnix };
}

/**
 * The key a trust-key fingerprint is pinned under.
 *
 * Byte-identical to Android `TrustKeyPinning.normalize` and its iOS port:
 * trim, strip **every** trailing slash, lowercase. A relay reached as
 * `https://Relay.test/` and `https://relay.test` must resolve to one pin, or
 * a cosmetic URL change would silently re-TOFU an already-pinned relay.
 */
export function normalizeRelayUrl(relayUrl: string): string {
  return relayUrl.trim().replace(/\/+$/, '').toLowerCase();
}

function decodeBase64Url(value: string): string | null {
  if (!BASE64URL_PATTERN.test(value)) return null;
  const base64 = value.replaceAll('-', '+').replaceAll('_', '/');
  const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), '=');
  let binary: string;
  try {
    binary = atob(padded);
  } catch {
    return null;
  }
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
  try {
    // `fatal` so a fragment that decodes to invalid UTF-8 is rejected instead
    // of becoming a payload full of U+FFFD that then fails the parse with the
    // wrong error copy.
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  } catch {
    return null;
  }
}

/**
 * Recover the payload from a `/pair#…` fragment.
 *
 * The agent base64url-encodes it (`pairing_qr.format_pairing_link`) because a
 * raw `|` is not a legal URL character and terminal linkifiers stop at it. The
 * padding is stripped on the way out and restored here; a hand-assembled
 * fragment carrying the payload literally is accepted too, since that costs
 * one branch and is exactly what someone debugging will type.
 */
export function decodePairingFragment(hash: string): string | null {
  const raw = hash.startsWith('#') ? hash.slice(1) : hash;
  if (raw === '') return null;
  let text = raw;
  try {
    text = decodeURIComponent(raw);
  } catch {
    // A malformed percent-escape means the fragment was never encoded; fall
    // back to the literal text rather than discarding a payload that parses.
    text = raw;
  }
  if (text.startsWith(PAYLOAD_PREFIX)) return text;
  return decodeBase64Url(text);
}

/**
 * Read whatever the user pasted into the pairing box.
 *
 * Whitespace goes first and goes **entirely**, including `\r\n`: the payload is
 * printed on one unbroken line, but a terminal soft-wraps it to the window
 * width and a copied soft-wrapped line carries the breaks along. No field of
 * the payload can contain whitespace, so stripping it is lossless.
 *
 * Three accepted shapes, because the agent prints the payload and the link
 * next to each other and both end up on clipboards: the payload itself, the
 * whole `https://…/pair#…` link, and a bare fragment.
 */
export function readPairingInput(text: string): string | null {
  const stripped = text.replace(/\s+/g, '');
  if (stripped === '') return null;
  if (stripped.startsWith(PAYLOAD_PREFIX)) return stripped;
  const hashIndex = stripped.lastIndexOf('#');
  if (hashIndex >= 0) return decodePairingFragment(stripped.slice(hashIndex + 1));
  return decodeBase64Url(stripped);
}

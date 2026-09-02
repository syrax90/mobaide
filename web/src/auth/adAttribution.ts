/**
 * Google Ads click attribution for account creation.
 *
 * When a visitor accepts the cookie banner on `mobaide.com`, the central server
 * sets a `mobaide_ads` cookie on the registrable domain: the click id the
 * visitor arrived with (`gclid`, or the iOS-era `wbraid` / `gbraid`), its kind
 * and the page-load time — or a bare consent marker when the landing URL
 * carried no click id — plus the server's HMAC signature over those fields.
 * The three account-creating auth calls forward it so the server can report
 * the account's first agent pairing back to the ad click.
 *
 * The signature is what makes the cookie mean anything: any same-site host
 * (the GitHub Pages `get.mobaide.com` included) can plant a domain cookie, and
 * the server alone decides, by verifying the signature, whether the value
 * counts as consent. This module therefore forwards the fields verbatim and
 * only guards the wire shape, mirroring the server's Pydantic bounds and
 * returning `null` for anything off instead of throwing — forwarding garbage
 * would turn a marketing cookie into a 422 on sign-up. The dev host never
 * sees a `.mobaide.com` cookie, so tests pass jar strings.
 */
import type { components } from '../api/generated/central-server';

export type AdAttribution = components['schemas']['AdAttribution'];
export type AdClickKind = NonNullable<AdAttribution['click_id_kind']>;

export const AD_ATTRIBUTION_COOKIE = 'mobaide_ads';

const CLICK_KINDS: ReadonlySet<string> = new Set<AdClickKind>(['gclid', 'wbraid', 'gbraid']);
const CLICK_ID_RE = /^[A-Za-z0-9_-]{1,200}$/;
const SIGNATURE_RE = /^[A-Za-z0-9_-]{16,128}$/;
const EPOCH_RE = /^\d{1,12}$/;
// The server's upper bound (2100-01-01); the lower bound is 0.
const EPOCH_MAX = 4_102_444_800;

/**
 * Exact-name lookup, parsed the way a browser does (see
 * `sessionTransport.ts`): strip ASCII blanks only, never `trim()`, and take
 * the first match when a name repeats.
 */
function cookieValue(jar: string, name: string): string | null {
  for (const entry of jar.split(';')) {
    const eq = entry.indexOf('=');
    if (eq === -1) continue;
    if (entry.slice(0, eq).replace(/^[ \t]+|[ \t]+$/g, '') === name) {
      return entry.slice(eq + 1);
    }
  }
  return null;
}

function isClickKind(value: string | null): value is AdClickKind {
  return value !== null && CLICK_KINDS.has(value);
}

function decodeBase64Url(encoded: string): string | null {
  try {
    return atob(encoded.replace(/-/g, '+').replace(/_/g, '/'));
  } catch {
    return null;
  }
}

/**
 * Parse the cookie's `<base64url(payload)>.<signature>` value, payload
 * `v=1&k=…&t=…&id=…`; `null` means "do not send".
 */
export function parseAdAttribution(raw: string): AdAttribution | null {
  const dot = raw.lastIndexOf('.');
  if (dot === -1) return null;
  const signature = raw.slice(dot + 1);
  if (!SIGNATURE_RE.test(signature)) return null;
  const payload = decodeBase64Url(raw.slice(0, dot));
  if (payload === null) return null;
  const params = new URLSearchParams(payload);
  if (params.get('v') !== '1') return null;
  const t = params.get('t');
  if (t === null || !EPOCH_RE.test(t) || Number(t) > EPOCH_MAX) return null;
  const clicked_at = Number(t);
  const kind = params.get('k');
  const id = params.get('id');
  if (kind === null && id === null) {
    return { click_id: null, click_id_kind: null, clicked_at, signature };
  }
  if (!isClickKind(kind) || id === null || !CLICK_ID_RE.test(id)) return null;
  return { click_id: id, click_id_kind: kind, clicked_at, signature };
}

/** Read the landing page's cookie; `null` when absent, unreadable or malformed. */
export function readAdAttribution(jar?: string): AdAttribution | null {
  let source = jar;
  if (source === undefined) {
    try {
      source = document.cookie;
    } catch {
      return null;
    }
  }
  const raw = cookieValue(source, AD_ATTRIBUTION_COOKIE);
  return raw === null ? null : parseAdAttribution(raw);
}

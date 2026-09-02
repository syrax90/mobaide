/**
 * Google Ads click attribution for account creation.
 *
 * `mobaide.com` writes a `mobaide_ads` cookie on the registrable domain when a
 * visitor accepts its cookie banner: the click id the visitor arrived with
 * (`gclid`, or the iOS-era `wbraid` / `gbraid`), its kind and a timestamp — or
 * a bare consent marker when the landing URL carried no click id. The three
 * account-creating auth calls forward it so the server can report the
 * account's first agent pairing back to the ad click.
 *
 * The validator mirrors the server's Pydantic model and is at least as strict,
 * degrading instead of throwing: any host on the site (the GitHub Pages
 * `get.mobaide.com` included) can plant this cookie, and forwarding a garbage
 * value would turn a marketing cookie into a 422 on sign-up. The dev host
 * never sees a `.mobaide.com` cookie, so tests pass jar strings.
 */
import type { components } from '../api/generated/central-server';

export type AdAttribution = components['schemas']['AdAttribution'];
export type AdClickKind = NonNullable<AdAttribution['click_id_kind']>;

export const AD_ATTRIBUTION_COOKIE = 'mobaide_ads';

const CLICK_KINDS: ReadonlySet<string> = new Set<AdClickKind>(['gclid', 'wbraid', 'gbraid']);
const CLICK_ID_RE = /^[A-Za-z0-9_-]{1,200}$/;
const EPOCH_RE = /^\d{1,12}$/;
// The server's upper bound (2100-01-01); the lower bound is 0.
const EPOCH_MAX = 4_102_444_800;

const CONSENT_ONLY: AdAttribution = { click_id: null, click_id_kind: null, clicked_at: null };

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

/** Parse the cookie's `v=1&k=…&t=…&id=…` value; `null` means "do not send". */
export function parseAdAttribution(raw: string): AdAttribution | null {
  const params = new URLSearchParams(raw);
  if (params.get('v') !== '1') return null;
  const kind = params.get('k');
  const id = params.get('id');
  if (!isClickKind(kind) || id === null || !CLICK_ID_RE.test(id)) {
    return { ...CONSENT_ONLY };
  }
  const t = params.get('t');
  const clicked_at = t !== null && EPOCH_RE.test(t) && Number(t) <= EPOCH_MAX ? Number(t) : null;
  return { click_id: id, click_id_kind: kind, clicked_at };
}

/** Read the landing page's cookie; `null` when absent, unreadable or invalid. */
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

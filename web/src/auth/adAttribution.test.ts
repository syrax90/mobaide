import { describe, expect, it, vi } from 'vitest';

import { AD_ATTRIBUTION_COOKIE, parseAdAttribution, readAdAttribution } from './adAttribution';

const CLICK = 'Cj0KCQjw_abc-123';
const SIGNATURE = 'a'.repeat(43);

/** The server's wire shape: base64url(payload) + '.' + signature. */
function cookie(payload: string, signature: string = SIGNATURE): string {
  const encoded = btoa(payload).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${encoded}.${signature}`;
}

const FULL = cookie(`v=1&k=gclid&t=1756800000&id=${CLICK}`);
const FULL_PARSED = { click_id: CLICK, click_id_kind: 'gclid', clicked_at: 1756800000, signature: SIGNATURE };

describe('readAdAttribution', () => {
  it('returns null for an empty jar', () => {
    expect(readAdAttribution('')).toBeNull();
  });

  it('ignores other cookies and near-miss names', () => {
    expect(readAdAttribution(`theme=dark; mobaide_ads_x=${FULL}; xmobaide_ads=${FULL}`)).toBeNull();
  });

  it('reads a full click cookie among others', () => {
    expect(readAdAttribution(`theme=dark;  ${AD_ATTRIBUTION_COOKIE}=${FULL}; other=1`)).toEqual(
      FULL_PARSED,
    );
  });

  it('takes the first match when the name repeats', () => {
    const first = cookie('v=1&k=wbraid&t=1&id=first');
    const second = cookie('v=1&k=gbraid&t=2&id=second');
    expect(readAdAttribution(`${AD_ATTRIBUTION_COOKIE}=${first}; ${AD_ATTRIBUTION_COOKIE}=${second}`)).toEqual(
      { click_id: 'first', click_id_kind: 'wbraid', clicked_at: 1, signature: SIGNATURE },
    );
  });

  it('reads document.cookie when no jar is given', () => {
    document.cookie = `${AD_ATTRIBUTION_COOKIE}=${FULL}`;
    try {
      expect(readAdAttribution()?.click_id).toBe(CLICK);
    } finally {
      document.cookie = `${AD_ATTRIBUTION_COOKIE}=; Max-Age=0`;
    }
  });

  it('returns null when the jar is unreadable', () => {
    const spy = vi.spyOn(document, 'cookie', 'get').mockImplementation(() => {
      throw new Error('denied');
    });
    try {
      expect(readAdAttribution()).toBeNull();
    } finally {
      spy.mockRestore();
    }
  });
});

describe('parseAdAttribution', () => {
  it('forwards a consent-only marker with its signature', () => {
    expect(parseAdAttribution(cookie('v=1&t=1756800000'))).toEqual({
      click_id: null,
      click_id_kind: null,
      clicked_at: 1756800000,
      signature: SIGNATURE,
    });
  });

  it('forwards the signature verbatim', () => {
    const signature = 'Z'.repeat(20) + '_-' + 'q'.repeat(21);
    expect(parseAdAttribution(cookie('v=1&t=5', signature))?.signature).toBe(signature);
  });

  it.each([
    ['no signature', 'djEmdD01'],
    ['a short signature', cookie('v=1&t=5', 'abc')],
    ['signature with bad characters', cookie('v=1&t=5', 'a'.repeat(20) + '$' + 'a'.repeat(20))],
    ['undecodable payload', `%%%.${SIGNATURE}`],
    ['an unknown version', cookie('v=2&k=gclid&t=1&id=abc')],
    ['a missing version', cookie('k=gclid&t=1&id=abc')],
    ['a missing timestamp', cookie('v=1&k=gclid&id=abc')],
    ['a negative timestamp', cookie('v=1&k=gclid&t=-5&id=abc')],
    ['a too-large timestamp', cookie('v=1&k=gclid&t=4102444801&id=abc')],
    ['an unknown kind', cookie('v=1&k=fbclid&t=1&id=abc')],
    ['forbidden characters in the id', cookie('v=1&k=gclid&t=1&id=a%20b')],
    ['an over-long id', cookie(`v=1&k=gclid&t=1&id=${'a'.repeat(201)}`)],
    ['a kind without an id', cookie('v=1&k=gclid&t=1')],
    ['an id without a kind', cookie('v=1&t=1&id=abc')],
  ])('returns null for %s instead of forwarding it', (_label, raw) => {
    expect(parseAdAttribution(raw)).toBeNull();
  });

  it('accepts the maximum timestamp the server accepts', () => {
    expect(parseAdAttribution(cookie('v=1&k=gclid&t=4102444800&id=abc'))?.clicked_at).toBe(
      4102444800,
    );
  });
});

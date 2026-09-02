import { describe, expect, it, vi } from 'vitest';

import { AD_ATTRIBUTION_COOKIE, parseAdAttribution, readAdAttribution } from './adAttribution';

const CLICK = 'Cj0KCQjw_abc-123';
const FULL = `v=1&k=gclid&t=1756800000&id=${CLICK}`;
const CONSENT_ONLY = { click_id: null, click_id_kind: null, clicked_at: null };

describe('readAdAttribution', () => {
  it('returns null for an empty jar', () => {
    expect(readAdAttribution('')).toBeNull();
  });

  it('ignores other cookies and near-miss names', () => {
    expect(readAdAttribution('theme=dark; mobaide_ads_x=v%3D1; xmobaide_ads=v=1')).toBeNull();
  });

  it('reads a full click cookie among others', () => {
    expect(readAdAttribution(`theme=dark;  ${AD_ATTRIBUTION_COOKIE}=${FULL}; other=1`)).toEqual({
      click_id: CLICK,
      click_id_kind: 'gclid',
      clicked_at: 1756800000,
    });
  });

  it('takes the first match when the name repeats', () => {
    const jar = `${AD_ATTRIBUTION_COOKIE}=v=1&k=wbraid&t=1&id=first; ${AD_ATTRIBUTION_COOKIE}=v=1&k=gbraid&t=2&id=second`;
    expect(readAdAttribution(jar)).toEqual({ click_id: 'first', click_id_kind: 'wbraid', clicked_at: 1 });
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
  it('maps a consent-only marker to nulls', () => {
    expect(parseAdAttribution('v=1&t=1756800000')).toEqual(CONSENT_ONLY);
  });

  it('rejects unknown or missing versions outright', () => {
    expect(parseAdAttribution('v=2&k=gclid&t=1&id=abc')).toBeNull();
    expect(parseAdAttribution('k=gclid&id=abc')).toBeNull();
  });

  it.each([
    ['an unknown kind', 'v=1&k=fbclid&t=1&id=abc'],
    ['forbidden characters', 'v=1&k=gclid&t=1&id=a%20b'],
    ['an over-long id', `v=1&k=gclid&t=1&id=${'a'.repeat(201)}`],
    ['a kind without an id', 'v=1&k=gclid&t=1'],
    ['an id without a kind', 'v=1&t=1&id=abc'],
  ])('degrades %s to consent-only instead of forwarding it', (_label, raw) => {
    expect(parseAdAttribution(raw)).toEqual(CONSENT_ONLY);
  });

  it.each([
    ['negative', '-5'],
    ['too large', '4102444801'],
    ['not a number', 'soon'],
  ])('drops an out-of-range timestamp (%s) but keeps the click', (_label, t) => {
    expect(parseAdAttribution(`v=1&k=gbraid&t=${t}&id=abc`)).toEqual({
      click_id: 'abc',
      click_id_kind: 'gbraid',
      clicked_at: null,
    });
  });

  it('accepts the maximum timestamp the server accepts', () => {
    expect(parseAdAttribution('v=1&k=gclid&t=4102444800&id=abc')?.clicked_at).toBe(4102444800);
  });
});

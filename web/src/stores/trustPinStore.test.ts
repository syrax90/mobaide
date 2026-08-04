import { beforeEach, describe, expect, it, vi } from 'vitest';

// Node >= 22 defines an experimental global `localStorage` that is undefined
// without --localstorage-file and shadows jsdom's implementation under vitest.
// Install a functional stand-in before importing the store so zustand's
// default createJSONStorage(() => window.localStorage) captures it.
const backing = new Map<string, string>();
vi.stubGlobal('localStorage', {
  getItem: (key: string) => backing.get(key) ?? null,
  setItem: (key: string, value: string) => {
    backing.set(key, String(value));
  },
  removeItem: (key: string) => {
    backing.delete(key);
  },
  clear: () => {
    backing.clear();
  },
  key: (index: number) => [...backing.keys()][index] ?? null,
  get length() {
    return backing.size;
  },
});

const { checkTrustPin, pinTrustKey, readTrustPin, useTrustPinStore } = await import(
  './trustPinStore',
);

/**
 * The pin-then-report pair the screen performs on a confirmed payload, kept
 * together here so the pre-split behaviour stays covered. The two halves are
 * separate in production precisely so validation cannot write (see
 * `checkTrustPin`), and the tests below that matter most assert exactly that.
 */
function confirmAndPin(relayUrl: string, fingerprint: string) {
  const result = checkTrustPin(relayUrl, fingerprint);
  pinTrustKey(relayUrl, fingerprint);
  return result;
}

const STORAGE_KEY = 'mobaide-trust-pins';
const FP_A = 'a'.repeat(64);
const FP_B = 'b'.repeat(64);

function seedStorage(pins: unknown) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ state: { pins }, version: 0 }));
}

beforeEach(() => {
  useTrustPinStore.setState({ pins: {} });
  window.localStorage.clear();
});

describe('checkTrustPin + pinTrustKey', () => {
  it('pins a relay it has never seen', () => {
    const result = confirmAndPin('https://relay.test', FP_A);

    expect(result).toEqual({ verdict: 'unpinned', pinned: null });
    expect(readTrustPin('https://relay.test')).toBe(FP_A);
  });

  it('reports a match when the same fingerprint comes back', () => {
    confirmAndPin('https://relay.test', FP_A);

    expect(confirmAndPin('https://relay.test', FP_A)).toEqual({
      verdict: 'match',
      pinned: FP_A,
    });
  });

  it('reports a mismatch and refuses to overwrite the pin', () => {
    // The whole value of trust-on-first-scan: a rotated key must not be able
    // to install itself by simply showing up. Android states the rule as
    // "callers MUST treat Mismatch as a hard refusal".
    confirmAndPin('https://relay.test', FP_A);

    const result = confirmAndPin('https://relay.test', FP_B);

    expect(result).toEqual({ verdict: 'mismatch', pinned: FP_A });
    expect(readTrustPin('https://relay.test')).toBe(FP_A);
  });

  it('keeps a repeated mismatch from ever wearing the pin down', () => {
    confirmAndPin('https://relay.test', FP_A);

    for (let attempt = 0; attempt < 5; attempt += 1) {
      expect(confirmAndPin('https://relay.test', FP_B).verdict).toBe('mismatch');
    }
    expect(readTrustPin('https://relay.test')).toBe(FP_A);
  });

  it('pins each relay independently', () => {
    confirmAndPin('https://relay.one', FP_A);

    expect(confirmAndPin('https://relay.two', FP_B).verdict).toBe('unpinned');
    expect(readTrustPin('https://relay.one')).toBe(FP_A);
  });

  it.each([
    'https://relay.test/',
    'https://relay.test///',
    'https://Relay.TEST',
    '  https://relay.test  ',
  ])('resolves %s to the same pin as its canonical form', (variant) => {
    // A cosmetic URL difference must not re-TOFU an already-pinned relay, or
    // the pin stops meaning anything.
    confirmAndPin('https://relay.test', FP_A);

    expect(confirmAndPin(variant, FP_A).verdict).toBe('match');
    expect(confirmAndPin(variant, FP_B).verdict).toBe('mismatch');
  });
});

describe('readTrustPin', () => {
  it('returns null for an unpinned relay', () => {
    expect(readTrustPin('https://relay.test')).toBeNull();
  });

  it('does not read an inherited Object property as a pin', () => {
    // The key is wire-derived. A bare `pins[key]` would resolve
    // `constructor` / `toString` to inherited functions and hand the caller a
    // "pin" that then mismatches every real fingerprint.
    expect(readTrustPin('constructor')).toBeNull();
    expect(readTrustPin('toString')).toBeNull();
    expect(readTrustPin('__proto__')).toBeNull();
  });
});

describe('trustPinStore hydration', () => {
  it('restores pins written by an earlier visit', async () => {
    seedStorage({ 'https://relay.test': FP_A });

    await useTrustPinStore.persist.rehydrate();

    expect(readTrustPin('https://relay.test')).toBe(FP_A);
  });

  it('does not let a __proto__ key in storage poison the pin map', async () => {
    // `localStorage` is user-editable and `JSON.parse` yields `__proto__` as
    // an own property, so a hand-edited entry would otherwise reach
    // `Object.prototype` and make every relay look pre-pinned.
    seedStorage({ __proto__: FP_B, 'https://relay.test': FP_A });

    await useTrustPinStore.persist.rehydrate();

    expect(readTrustPin('https://relay.other')).toBeNull();
    expect(confirmAndPin('https://relay.other', FP_A).verdict).toBe('unpinned');
    expect(({} as Record<string, unknown>)['polluted']).toBeUndefined();
    expect(readTrustPin('https://relay.test')).toBe(FP_A);
  });

  it('drops entries whose fingerprint is not a non-empty string', async () => {
    seedStorage({
      'https://relay.good': FP_A,
      'https://relay.numeric': 42,
      'https://relay.null': null,
      'https://relay.empty': '',
      '': FP_B,
    });

    await useTrustPinStore.persist.rehydrate();

    expect(readTrustPin('https://relay.good')).toBe(FP_A);
    expect(readTrustPin('https://relay.numeric')).toBeNull();
    expect(readTrustPin('https://relay.null')).toBeNull();
    expect(readTrustPin('https://relay.empty')).toBeNull();
    expect(useTrustPinStore.getState().pins).toEqual({ 'https://relay.good': FP_A });
  });

  it('starts empty when the persisted pins are not an object', async () => {
    seedStorage('not-an-object');

    await useTrustPinStore.persist.rehydrate();

    expect(useTrustPinStore.getState().pins).toEqual({});
  });

  it('survives a round trip through storage', async () => {
    confirmAndPin('https://relay.test', FP_A);
    // The raw envelope has to be re-seeded by hand: `setState` writes straight
    // back through the persist middleware, so clearing the store in memory
    // would erase the very record this test is about to rehydrate.
    const persisted = window.localStorage.getItem(STORAGE_KEY);
    useTrustPinStore.setState({ pins: {} });
    window.localStorage.setItem(STORAGE_KEY, persisted ?? '');

    await useTrustPinStore.persist.rehydrate();

    expect(readTrustPin('https://relay.test')).toBe(FP_A);
  });

  it('persists under the documented localStorage key', () => {
    // `mobaide-trust-pins` is in `web/README.md` and `docs/qr-pairing.md` as
    // the thing a user clears to re-pin a legitimately rotated relay key —
    // the only recovery path there is, since a mismatch has no override.
    confirmAndPin('https://relay.test', FP_A);

    expect(window.localStorage.getItem(STORAGE_KEY)).toContain(FP_A);
  });
});

describe('checkTrustPin never writes', () => {
  it('leaves an unseen relay unpinned', () => {
    // The property the read/write split exists for. `/pair` evaluates
    // arbitrary payloads straight off a URL, so if inspection pinned, anyone
    // could mail a link that permanently poisons the pin for the real relay —
    // and a mismatch is a hard refusal with no override, so the victim would
    // be locked out of pairing until they cleared site data.
    expect(checkTrustPin('https://relay.test', FP_A).verdict).toBe('unpinned');

    expect(readTrustPin('https://relay.test')).toBeNull();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it('does not adopt a rejected fingerprint even when asked to pin it', () => {
    pinTrustKey('https://relay.test', FP_A);

    pinTrustKey('https://relay.test', FP_B);

    expect(readTrustPin('https://relay.test')).toBe(FP_A);
  });
});

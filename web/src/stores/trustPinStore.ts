import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { normalizeRelayUrl } from '../protocol/pairingPayload';

export type TrustPinVerdict = 'unpinned' | 'match' | 'mismatch';

export interface TrustPinResult {
  verdict: TrustPinVerdict;
  /** What was already on file. Only set for `mismatch`, for the refusal copy. */
  pinned: string | null;
}

interface TrustPinState {
  /** normalized relay URL → the trust-key fingerprint first seen for it. */
  pins: Record<string, string>;
  setPin: (relayUrl: string, fingerprint: string) => void;
}

/**
 * Add one entry to a fresh record without tripping the prototype setter.
 *
 * A relay URL is wire-derived, and `target[key] = value` with
 * `key === '__proto__'` invokes `Object.prototype`'s setter instead of adding
 * an own property — losing the entry and re-parenting the object.
 */
function defineEntry(target: Record<string, string>, key: string, value: string): void {
  Object.defineProperty(target, key, {
    value,
    enumerable: true,
    writable: true,
    configurable: true,
  });
}

/**
 * Trust-On-First-Scan pins, one per relay.
 *
 * The port of Android's `mobaide_trust_pins` DataStore and the iOS
 * `UserDefaults` suite of the same name. `localStorage`, not `sessionStorage`:
 * a pin that died with the tab would re-TOFU on every visit, which is not a
 * pin at all.
 *
 * **The browser is a weaker home for this than a phone, and knowingly so.**
 * Safari caps script-written storage at 7 days of no interaction, so an
 * infrequently used relay silently degrades from trust-on-first-scan to
 * trust-on-most-recent-scan (and the same wipe regenerates the device UUID,
 * which under the registry's last-login-wins rule orphans a device row). The
 * durable fix is the account-level server-side pin in CLA-199; until then this
 * is documented in `docs/qr-pairing.md` rather than hidden.
 */
export const useTrustPinStore = create<TrustPinState>()(
  persist(
    (set) => ({
      pins: {},
      setPin: (relayUrl, fingerprint) => {
        set((state) => {
          const next: Record<string, string> = {};
          for (const [pinnedUrl, pinnedFp] of Object.entries(state.pins)) {
            defineEntry(next, pinnedUrl, pinnedFp);
          }
          defineEntry(next, normalizeRelayUrl(relayUrl), fingerprint);
          return { pins: next };
        });
      },
    }),
    {
      name: 'mobaide-trust-pins',
      // Same untrusted-boundary rule as `lastSessionStore`: `localStorage` is
      // editable and `JSON.parse` yields `__proto__` as an own key, so the
      // rehydrated map is rebuilt entry by entry with the types checked.
      merge: (persisted, current) => {
        const stored = (persisted as Partial<TrustPinState> | undefined)?.pins;
        const clean: Record<string, string> = {};
        if (typeof stored === 'object' && stored !== null) {
          for (const [relayUrl, fingerprint] of Object.entries(stored)) {
            if (relayUrl !== '' && typeof fingerprint === 'string' && fingerprint !== '') {
              defineEntry(clean, relayUrl, fingerprint);
            }
          }
        }
        return { ...current, pins: clean };
      },
    },
  ),
);

/** Non-reactive read of the fingerprint pinned for a relay, if any. */
export function readTrustPin(relayUrl: string): string | null {
  const { pins } = useTrustPinStore.getState();
  const key = normalizeRelayUrl(relayUrl);
  // `Object.hasOwn`, not a bare index: the key is wire-derived, and `pins.constructor`
  // would otherwise resolve to an inherited function and read as a pin.
  return Object.hasOwn(pins, key) ? (pins[key] ?? null) : null;
}

/**
 * Compare a fingerprint against what is on file. **Never writes.**
 *
 * The read and the write are separate calls on purpose. Both mobile clients
 * fold them into one `verifyOrPin` that pins on first sight, which is safe
 * when the only input channel is a camera the user is aiming. On web the input
 * is a URL: folding them would let any link the user merely *opens* — before
 * confirming anything, before the relay is ever contacted — write a permanent
 * pin for an arbitrary relay. Since a mismatch is a hard refusal with no
 * override, an attacker-authored payload naming the real relay with a wrong
 * fingerprint would lock the victim out of pairing for good, recoverable only
 * by clearing site data. So the pin is written from the confirmed path
 * (`pinTrustKey`), and this comparison runs on every candidate payload.
 *
 * A mismatch never overwrites and has no override affordance anywhere in the
 * UI — Android's `TrustKeyPinning` KDoc states the rule as "callers MUST treat
 * Mismatch as a hard refusal — do NOT prompt the user for confirmation", and a
 * browser is not the place to soften it.
 */
export function checkTrustPin(relayUrl: string, fingerprint: string): TrustPinResult {
  const existing = readTrustPin(relayUrl);
  if (existing === null) return { verdict: 'unpinned', pinned: null };
  if (existing === fingerprint) return { verdict: 'match', pinned: existing };
  return { verdict: 'mismatch', pinned: existing };
}

/**
 * Record the trust-on-first-use pin, once the user has confirmed the pairing.
 *
 * Idempotent for a matching fingerprint, and deliberately a no-op on mismatch:
 * the caller has already refused, and a write here would silently adopt the
 * fingerprint the refusal exists to reject.
 */
export function pinTrustKey(relayUrl: string, fingerprint: string): void {
  if (checkTrustPin(relayUrl, fingerprint).verdict === 'mismatch') return;
  useTrustPinStore.getState().setPin(relayUrl, fingerprint);
}

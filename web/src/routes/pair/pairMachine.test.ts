import { describe, expect, it, vi } from 'vitest';
import type { TrustPinResult } from '../../stores/trustPinStore';
import { MALFORMED_PAYLOAD_MESSAGE } from './pairCopy';
import { evaluatePairingInput } from './pairMachine';

const NOW = 1_700_000_000;
const FP = 'f'.repeat(64);

const payload = (over: Partial<Record<'relay' | 'nonce' | 'fp' | 'exp', string>> = {}) =>
  [
    'MOBA1',
    over.relay ?? 'https://relay.test',
    over.nonce ?? 'nonce-abc',
    over.fp ?? FP,
    over.exp ?? String(NOW + 300),
  ].join('|');

/** Records every call so the "never asked" assertions can be exact. */
function verifier(verdict: TrustPinResult['verdict'], pinned: string | null = null) {
  return vi.fn<(relayUrl: string, fingerprint: string) => TrustPinResult>(() => ({
    verdict,
    pinned,
  }));
}

describe('evaluatePairingInput', () => {
  it('advances a fresh, first-seen payload to the confirmation step', () => {
    const checkTrust = verifier('unpinned');

    const state = evaluatePairingInput(payload(), NOW, checkTrust);

    expect(state).toEqual({
      kind: 'confirming',
      payload: {
        version: 'MOBA1',
        relayUrl: 'https://relay.test',
        nonce: 'nonce-abc',
        trustKeyFp: FP,
        expUnix: NOW + 300,
      },
    });
    expect(checkTrust).toHaveBeenCalledWith('https://relay.test', FP);
  });

  it('advances a payload from an already-pinned relay', () => {
    const state = evaluatePairingInput(payload(), NOW, verifier('match', FP));

    expect(state.kind).toBe('confirming');
  });

  it('reads a whole pairing link, not just a bare payload', () => {
    // `readPairingInput` runs first, so the paste box and the deep link feed
    // one pipeline rather than two that can drift apart.
    const fragment = btoa(payload()).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '');
    const link = `https://app.mobaide.com/pair#${fragment}`;

    expect(evaluatePairingInput(link, NOW, verifier('unpinned')).kind).toBe('confirming');
  });

  it('rejects unreadable input before anything else runs', () => {
    const checkTrust = verifier('unpinned');

    const state = evaluatePairingInput('not a pairing code', NOW, checkTrust);

    expect(state).toEqual({ kind: 'error', message: MALFORMED_PAYLOAD_MESSAGE });
    expect(checkTrust).not.toHaveBeenCalled();
  });

  it('reports a well-formed-looking but unparseable payload as malformed', () => {
    // Same undifferentiated message as unreadable input: both mobile clients
    // surface one string, and a caller must not be able to leak the
    // difference into copy the other platforms do not have.
    const state = evaluatePairingInput('MOBA2|r|n|f|100', NOW, verifier('unpinned'));

    expect(state).toEqual({ kind: 'error', message: MALFORMED_PAYLOAD_MESSAGE });
  });

  it('checks expiry before the trust key, so a stale code never reaches the pin store', () => {
    // The ordering Android pins as `skipsTrustKeyCheck`. If the trust check
    // ran first, a screenshot taken before a legitimate key rotation would
    // trip the MITM refusal instead of saying "this code expired".
    const checkTrust = verifier('mismatch', 'a'.repeat(64));

    const state = evaluatePairingInput(
      payload({ exp: String(NOW - 600) }),
      NOW,
      checkTrust,
    );

    expect(checkTrust).not.toHaveBeenCalled();
    expect(state.kind).toBe('error');
    expect(state.kind === 'error' && state.message).toContain('expired');
    expect(state.kind === 'error' && state.message).not.toContain('MITM');
  });

  it('treats a payload expiring exactly now as expired', () => {
    const state = evaluatePairingInput(payload({ exp: String(NOW) }), NOW, verifier('unpinned'));

    expect(state.kind).toBe('error');
    expect(state.kind === 'error' && state.message).toContain('expired');
  });

  it('never pins a fingerprint for a payload it is about to reject', () => {
    // The verifier is injected precisely so validating input cannot have the
    // side effect of pinning: a malformed or stale code must leave the TOFS
    // store untouched.
    const checkTrust = verifier('unpinned');

    evaluatePairingInput('garbage', NOW, checkTrust);
    evaluatePairingInput(payload({ exp: String(NOW - 1) }), NOW, checkTrust);

    expect(checkTrust).not.toHaveBeenCalled();
  });

  it('refuses a fingerprint mismatch and names the relay', () => {
    const state = evaluatePairingInput(payload(), NOW, verifier('mismatch', 'a'.repeat(64)));

    expect(state.kind).toBe('error');
    expect(state.kind === 'error' && state.message).toContain('https://relay.test');
    expect(state.kind === 'error' && state.message).toContain('MITM');
  });

  it('verifies against the un-normalized relay url from the payload', () => {
    // Normalisation is the pin store's job, not the machine's: doing it here
    // too would mean two normalisers to keep byte-identical with Android.
    const checkTrust = verifier('unpinned');

    evaluatePairingInput(payload({ relay: 'https://Relay.Test/' }), NOW, checkTrust);

    expect(checkTrust).toHaveBeenCalledWith('https://Relay.Test/', FP);
  });
});

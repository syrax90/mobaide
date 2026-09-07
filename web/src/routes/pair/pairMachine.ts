/**
 * The pairing screen's state, as a pure module.
 *
 * Mirrors Android's `QrPairingState` (Idle / Confirming / Claiming /
 * WaitingForOnline / WaitTimedOut / Success / Error) and its iOS port. `error`
 * is a refused or unsent claim; `waitTimedOut` is a claim that succeeded whose
 * agent has not connected back yet — it stays paired, and "Keep waiting"
 * re-enters `waitingForOnline` with the next `attempt`. The signed-out case
 * has no state of its own: a deep link can land on a signed-out browser, but
 * `Pair.tsx` redirects to sign-in and renders nothing, so the machine never
 * observes it.
 *
 * Keeping the gate pure is what makes the ordering testable. It matters: the
 * expiry check runs **before** the trust check on both mobile clients (Android
 * pins it in `AgentListViewModelQrPairingTest.skipsTrustKeyCheck`), so a stale
 * code is never compared against the pin store — otherwise a screenshot from
 * before a legitimate key rotation would trip the MITM refusal.
 */
import type { ParsedPairingPayload } from '../../protocol/pairingPayload';
import { parseMoba1, readPairingInput } from '../../protocol/pairingPayload';
import type { TrustPinResult } from '../../stores/trustPinStore';
import {
  MALFORMED_PAYLOAD_MESSAGE,
  expiredMessage,
  trustMismatchMessage,
} from './pairCopy';

export type PairState =
  | { kind: 'idle' }
  | { kind: 'confirming'; payload: ParsedPairingPayload }
  | { kind: 'claiming'; payload: ParsedPairingPayload }
  | {
      kind: 'waitingForOnline';
      agentId: string;
      friendlyName: string;
      wsDelivered: boolean | null;
      /** Bumped by "Keep waiting" so the watcher restarts its clock. */
      attempt: number;
    }
  | {
      kind: 'waitTimedOut';
      agentId: string;
      friendlyName: string;
      wsDelivered: boolean | null;
      attempt: number;
    }
  | { kind: 'success'; agentId: string; friendlyName: string }
  | { kind: 'error'; message: string };

/**
 * Turn raw pasted or deep-linked text into the next state.
 *
 * The trust check is injected rather than imported so the ordering above can be
 * asserted without a storage stub. It must be a **read** — this function runs
 * on unconfirmed, attacker-authorable input, so pinning here would let a link
 * write a permanent trust decision (see `checkTrustPin`).
 */
export function evaluatePairingInput(
  raw: string,
  nowSeconds: number,
  checkTrust: (relayUrl: string, fingerprint: string) => TrustPinResult,
): PairState {
  const text = readPairingInput(raw);
  if (text === null) return { kind: 'error', message: MALFORMED_PAYLOAD_MESSAGE };
  const payload = parseMoba1(text);
  if (payload === null) return { kind: 'error', message: MALFORMED_PAYLOAD_MESSAGE };
  if (payload.expUnix <= nowSeconds) {
    return { kind: 'error', message: expiredMessage(payload.expUnix, nowSeconds) };
  }
  const trust = checkTrust(payload.relayUrl, payload.trustKeyFp);
  if (trust.verdict === 'mismatch') {
    return { kind: 'error', message: trustMismatchMessage(payload.relayUrl) };
  }
  return { kind: 'confirming', payload };
}

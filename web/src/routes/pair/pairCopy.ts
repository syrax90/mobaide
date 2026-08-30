/**
 * Every string the pairing screen can show, as pure functions.
 *
 * Mostly lifted from `AgentListViewModel` (Android) and `AgentListModel`
 * (iOS). Three deliberate divergences, each noted at its definition: the
 * malformed-input message (web has no camera, so "scanned QR" is wrong), the
 * expiry message (the issue asks for the elapsed time a paste box can compute
 * but a scanner cannot), and the trust-mismatch recovery step (a browser has
 * no "clear app data").
 */
import { isApiError } from '../../api/errors';

/** Both mobile clients cap the post-claim wait here; the agent's own write-retry ceiling. */
export const WAIT_FOR_ONLINE_TIMEOUT_SECONDS = 60;

/**
 * Android says "Scanned QR is not a Mobaide pairing code"; there is nothing to
 * scan here, and the two things a user can paste are named instead — a paste
 * box can be wrong in ways a camera cannot.
 */
export const MALFORMED_PAYLOAD_MESSAGE =
  'That is not a Mobaide pairing code. Copy the whole MOBA1|… line the agent printed, or the link next to it.';

const FRESH_CODE_HINT =
  'On the agent host, run `mobaide-agent show-qr` to render a fresh one.';

/**
 * Mobile shows a flat "QR expired. Render a fresh one on the agent." A pasted
 * payload can be arbitrarily stale — a screenshot from last week, a scrollback
 * line from this morning — so the elapsed time is the part that tells the user
 * whether they mis-copied or simply waited too long.
 */
export function expiredMessage(expUnix: number, nowSeconds: number): string {
  const elapsed = Math.max(0, nowSeconds - expUnix);
  const minutes = Math.floor(elapsed / 60);
  const ago =
    minutes < 1
      ? 'less than a minute ago'
      : minutes === 1
        ? '1 minute ago'
        : minutes < 120
          ? `${String(minutes)} minutes ago`
          : `${String(Math.floor(minutes / 60))} hours ago`;
  return `This pairing code expired ${ago}. ${FRESH_CODE_HINT}`;
}

/**
 * How long a still-valid code has left, for the confirmation step.
 *
 * Mobile prints the raw `exp_unix` there. That is readable on a phone held
 * next to the terminal that produced it, and useless in a browser tab opened
 * from a link some minutes later.
 */
export function expiresInMessage(expUnix: number, nowSeconds: number): string {
  const minutes = Math.floor(Math.max(0, expUnix - nowSeconds) / 60);
  if (minutes < 1) return 'in under a minute';
  if (minutes === 1) return 'in 1 minute';
  return `in ${String(minutes)} minutes`;
}

/**
 * Byte-identical to Android and iOS up to the final sentence, which names the
 * browser's equivalent of clearing app data. No override anywhere: a mismatch
 * is a hard refusal on every platform.
 */
export function trustMismatchMessage(relayUrl: string): string {
  return (
    `Trust key changed for ${relayUrl} — possible MITM. Refusing to claim. ` +
    "If you intentionally rotated the relay key, clear this site's data to re-pin."
  );
}

/** `detail.error` on the relay's account-trust-pin refusal. */
export const TRUST_PIN_MISMATCH_CODE = 'trust_key_pin_mismatch';

/**
 * The account-level pin refusal — a different failure from
 * {@link trustMismatchMessage}, which is this browser's own local pin.
 *
 * Recovery is deliberately not self-service. The pin only ever disagrees when
 * the relay's signing key changed, and nobody signed in to a browser can tell
 * an intended rotation from a compromise; the operator can.
 */
export function trustPinMismatchMessage(): string {
  return (
    "This relay's signing key is not the one this account first paired with. " +
    'Pairing is refused. Compare the fingerprint on a machine you already ' +
    'paired (`mobaide-agent show-trust-fp`) and contact the relay operator if ' +
    'they differ.'
  );
}

/** Why a confirm is going to be refused, known before the user clicks. */
export type DeviceCodeBlockedReason = 'unknown_agent' | 'owned_by_other';

/**
 * The blocked-reason copy, and the canonical wording for both refusals.
 *
 * `deviceCodeErrorMessage` reuses these for the matching statuses rather than
 * restating them: the poll shows the reason before the click and the server
 * repeats it after, so two wordings for one condition is how they drift.
 */
export function deviceCodeBlockedMessage(reason: DeviceCodeBlockedReason): string {
  switch (reason) {
    case 'unknown_agent':
      return (
        'That machine still has credentials for an agent this relay no longer has. ' +
        'On the agent host run `mobaide-agent re-pair --fresh`, then enter a new code.'
      );
    case 'owned_by_other':
      return (
        'That machine is already paired to a different account. Its current owner has ' +
        'to remove it before it can be paired here.'
      );
  }
}

/**
 * Failures of the browser-mints-the-code flow.
 *
 * Distinct from {@link claimErrorMessage} because the remedies differ: nothing
 * here is fixed by re-rendering a QR on the agent, and every dead end is fixed
 * by minting a fresh code right here.
 */
export function deviceCodeErrorMessage(error: unknown): string {
  if (!isApiError(error)) {
    return error instanceof Error && error.message !== ''
      ? error.message
      : 'Pairing failed.';
  }
  if (error.code === TRUST_PIN_MISMATCH_CODE) return trustPinMismatchMessage();
  switch (error.status) {
    case 404:
      return 'This pairing request no longer exists. Get a new code.';
    case 409:
      return deviceCodeBlockedMessage('owned_by_other');
    case 410:
      return 'This pairing request expired or was already used. Get a new code.';
    case 429:
      return 'Too many pairing attempts. Wait a moment and try again.';
    case 503:
      return (
        'Pairing is not configured on this relay — it is missing its manifest ' +
        'signing keys. Contact the relay operator.'
      );
    default:
      return error.message !== '' ? error.message : 'Pairing failed.';
  }
}

/**
 * Claim failures, by status.
 *
 * Both mobile clients special-case only 410 and pass every other server
 * `detail` straight through. That is a worse deal in a browser: a phone user
 * is usually sitting at the agent host, while a desktop user often is not, and
 * none of these details name a remedy. So each status the claim handler can
 * raise gets copy that does.
 */
export function claimErrorMessage(error: unknown): string {
  if (!isApiError(error)) {
    return error instanceof Error && error.message !== ''
      ? error.message
      : 'Pairing failed.';
  }
  if (error.code === TRUST_PIN_MISMATCH_CODE) return trustPinMismatchMessage();
  switch (error.status) {
    case 400:
      return `This pairing code was built with an older relay trust key. ${FRESH_CODE_HINT}`;
    case 404:
      return (
        'The agent this code belongs to no longer exists on the relay. On the agent ' +
        'host, run `mobaide-agent re-pair --fresh`, then pair again.'
      );
    case 409:
      return (
        'This agent is already paired to a different account. Its current owner has ' +
        'to remove it before it can be paired here.'
      );
    case 410:
      // Verbatim from both mobile clients.
      return 'Pairing code consumed or expired. Render a fresh QR on the agent.';
    case 503:
      return (
        'Pairing is not configured on this relay — it is missing its manifest ' +
        'signing keys. Contact the relay operator.'
      );
    default:
      return error.message !== '' ? error.message : 'Pairing failed.';
  }
}

/**
 * iOS's shape, not Android's.
 *
 * Not a failure message: the claim landed and the agent stays paired, so the
 * copy says so and points at the list that will flip it Online on its own.
 * Shared word-for-word with Android (`buildWaitTimeoutMessage`) and iOS
 * (`AgentListModel.waitTimeoutMessage`), and neutral about the agent host —
 * none of the three clients can tell whether it runs a Windows task, a
 * systemd unit, or a launchd job.
 *
 * `wsDelivered` is tri-state and `null` (an older relay that omits the field)
 * appends nothing rather than claiming the push was staged.
 */
export function waitTimeoutMessage(
  friendlyName: string,
  wsDelivered: boolean | null,
): string {
  const parts = [
    `‘${friendlyName}’ was paired to your account but hasn't connected within ${String(WAIT_FOR_ONLINE_TIMEOUT_SECONDS)} s. It will show as Online in your agents list as soon as its relay client connects.`,
  ];
  if (wsDelivered === true) {
    parts.push("The relay delivered credentials to the agent, but its tunnel hasn't connected yet.");
  } else if (wsDelivered === false) {
    parts.push("Credentials were staged for the agent's next reconnect.");
  }
  parts.push(
    'Check that the agent service is running on that host, or run `mobaide-agent show-qr` there to restart pairing.',
  );
  return parts.join(' ');
}

/** Heading over `waitTimeoutMessage`; deliberately not "Pairing failed". */
export const WAIT_TIMEOUT_TITLE = 'Agent not online yet';

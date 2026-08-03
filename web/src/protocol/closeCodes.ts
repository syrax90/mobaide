/**
 * Application-private WebSocket close codes (RFC 6455 §7.4.2, range 4000–4999).
 *
 * Version-independent: they are not tied to any `protocol_version` and apply to
 * every proto-v revision. The authoritative table is PROTOCOL.md; the values
 * are pinned against `tests/protocol_golden.json` by `closeCodes.test.ts`.
 *
 * Browser caveat, verified against the server: `4001` and `4010` are sent
 * *before* `websocket.accept()` (`central_server/central_server/auth/dependencies.py`),
 * and ASGI turns a pre-accept close into an HTTP 403 handshake rejection. The
 * browser WebSocket API exposes no upgrade status, so those two arrive as
 * `1006` instead. `4003` and `4012` are sent after accept and do arrive
 * verbatim, as do `4010`/`4011` on an already-open socket. The table below is
 * therefore correct but only partly reachable in a browser — see
 * `src/realtime/closePolicy.ts`.
 */
export const WS_CLOSE = {
  /** Normal closure. The only code a browser client may send besides 3000–4999. */
  normal: 1000,
  /** JWT missing, expired, or invalid. Terminal on the user axis. */
  authFailed: 4001,
  /** Client `protocol_version` outside the server's supported set. Terminal. */
  protocolIncompatible: 4002,
  /** Token not valid for this agent, or the user does not own it. Agent axis only. */
  tokenNotValidForAgent: 4003,
  /** User/agent destructively removed. Terminal; credentials are gone for good. */
  adminRevoked: 4010,
  /** Non-destructive admin boot. Credentials remain valid — reconnect. */
  adminForceDisconnect: 4011,
  /** Agent tunnel not currently connected. Transient — keep backing off. */
  agentTransientOffline: 4012,
  /** Relay-side "agent busy" on the autoscan channel. Transient. */
  agentBusy: 1013,
} as const;

export type WsCloseCode = (typeof WS_CLOSE)[keyof typeof WS_CLOSE];

/**
 * Codes a browser is allowed to pass to `WebSocket.close()`.
 *
 * Anything else throws `InvalidAccessError` (WHATWG WebSocket §close), which is
 * why the channel manager only ever closes with `WS_CLOSE.normal`.
 */
export function isSendableCloseCode(code: number): boolean {
  return code === WS_CLOSE.normal || (code >= 3000 && code <= 4999);
}

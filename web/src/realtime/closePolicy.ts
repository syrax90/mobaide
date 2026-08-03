/**
 * What a close code means, per connection axis.
 *
 * The axis matters. On the user axis a rejected credential ends the session; on
 * the agent axis it does not, because the per-agent `mobile_access` token is
 * always re-mintable from the user session — Android builds its agent-scoped
 * connections with no `onAuthFailure` callback at all, and iOS's `AgentEventBus`
 * turns the terminal outcomes into a bare `disconnected`.
 *
 * Reachability caveat: `4001` and `4010` are sent before `websocket.accept()`,
 * which ASGI turns into an HTTP 403 handshake rejection, and the browser
 * WebSocket API exposes no upgrade status — so in a browser they usually arrive
 * as `1006`. The mapping is still implemented because it is correct wherever
 * the code does arrive (an already-open socket revoked by an admin, and any
 * future post-accept rejection), and because the REST 401 path is what actually
 * tears a dead session down.
 */
import { WS_CLOSE } from '../protocol/closeCodes';

export type ChannelAxis = 'user' | 'agent';

/** A close the channel must surface as more than "the socket went away". */
export type TerminalOutcome =
  | { kind: 'tokenRejected' }
  | { kind: 'accountRemoved' }
  | { kind: 'incompatibleServer'; reason: string };

export interface ClosePolicy {
  /** Whether the backoff loop keeps running. */
  reconnect: boolean;
  /** Event to emit after `disconnected`, or null. */
  terminal: TerminalOutcome | null;
  /** Drop this agent's cached token so the next connect re-mints. */
  clearAgentToken: boolean;
}

const RECONNECT: ClosePolicy = { reconnect: true, terminal: null, clearAgentToken: false };
const STOP: ClosePolicy = { reconnect: false, terminal: null, clearAgentToken: false };

export function closePolicyFor(
  code: number,
  axis: ChannelAxis,
  reason: string,
): ClosePolicy {
  switch (code) {
    case WS_CLOSE.authFailed:
      return axis === 'user'
        ? { reconnect: false, terminal: { kind: 'tokenRejected' }, clearAgentToken: false }
        : { reconnect: false, terminal: null, clearAgentToken: true };

    case WS_CLOSE.protocolIncompatible:
      return axis === 'user'
        ? {
            reconnect: false,
            terminal: {
              kind: 'incompatibleServer',
              reason: reason === '' ? 'Server protocol incompatible' : reason,
            },
            clearAgentToken: false,
          }
        : STOP;

    case WS_CLOSE.adminRevoked:
      return axis === 'user'
        ? { reconnect: false, terminal: { kind: 'accountRemoved' }, clearAgentToken: false }
        : STOP;

    // The token was minted for a different agent, or ownership changed. Dropping
    // it and reconnecting re-mints on the next attempt. If ownership really is
    // gone the mint keeps failing (`POST /api/agents/{id}/token` answers 403),
    // and the channel stops after `MAX_UNBUILDABLE_URL_ATTEMPTS` — the agent
    // axis retries a null URL because it usually means a transient mint
    // failure, so the bound is what keeps a permanent 403 from looping.
    case WS_CLOSE.tokenNotValidForAgent:
      return axis === 'agent'
        ? { reconnect: true, terminal: null, clearAgentToken: true }
        : RECONNECT;

    // 4011 admin force-disconnect, 4012 agent transient offline (the ordinary
    // attach-to-an-offline-agent case, which must stay quiet), 1013 relay
    // "agent busy", 1000 server-initiated normal close, 1006 abnormal — all
    // recoverable.
    default:
      return RECONNECT;
  }
}

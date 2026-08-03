/**
 * Relay path rewrite and WebSocket URL assembly.
 *
 * Port of iOS `RelayRouting.swift` / Android `WebSocketManager.mapToRelayPath`,
 * pinned byte-for-byte by `tests/protocol_fixtures/relay/relay_mapping.json`.
 * iOS wins where the two mobile clients differ: it trims trailing slashes off
 * the base URL, Android does not.
 *
 * The web SPA is same-origin with the relay (WEB-02), so callers pass
 * `window.location.origin` as the base and never a remote host.
 */

/**
 * Which side of the two-axis split a connection uses.
 *
 * `relay` rewrites the path to the per-agent relay endpoint; `direct` addresses
 * the path as-is. The user-events channel is built in `direct` mode *even
 * against the relay base* — that is the deliberate asymmetry both fixture rows
 * (`relay_fallback_path` and `user_events_no_rewrite_on_relay_base`) encode.
 */
export type ConnectionMode = 'direct' | 'relay';

function trimTrailingSlashes(value: string): string {
  let end = value.length;
  while (end > 0 && value[end - 1] === '/') end -= 1;
  return value.slice(0, end);
}

function stripScheme(base: string): string {
  if (base.startsWith('https://')) return base.slice('https://'.length);
  if (base.startsWith('http://')) return base.slice('http://'.length);
  return base;
}

/**
 * Rewrite an agent-relative WS path to its relay equivalent.
 *
 * Note the ordering in the session case: the agent id comes *before* the
 * session id. Unrecognised paths fall through to `relay/{path}/{agentId}`,
 * which is what makes `ws/user-events` land at `relay/ws/user-events/{agentId}`
 * if it is ever built in relay mode — the reason the user channel uses `direct`.
 */
export function mapToRelayPath(path: string, agentId: string): string {
  if (path.startsWith('ws/session/')) {
    const sessionId = path.slice('ws/session/'.length);
    return `relay/ws/session/${agentId}/${sessionId}`;
  }
  if (path === 'ws/events') return `relay/ws/events/${agentId}`;
  if (path === 'ws/sessions') return `relay/ws/sessions/${agentId}`;
  if (path === 'ws/autoscan') return `relay/ws/autoscan/${agentId}`;
  return `relay/${path}/${agentId}`;
}

export interface WebSocketUrlSpec {
  /** Origin the SPA is served from, e.g. `https://app.mobaide.com`. */
  base: string;
  mode: ConnectionMode;
  /**
   * Agent-relative path with no leading slash, e.g. `ws/session/s1`.
   *
   * **Already percent-encoded by the caller** — see `src/realtime/channels.ts`
   * for the reference. These builders stay encoding-free so their output
   * compares byte-for-byte against `relay/relay_mapping.json`, which is shared
   * with the iOS test target.
   */
  path: string;
  /** Required in `relay` mode; ignored in `direct`. Pre-encoded, as `path`. */
  agentId?: string | null;
  token: string;
}

/**
 * Assemble the full `ws(s)://…?token=…` URL.
 *
 * Two rules the golden table pins and that are easy to get wrong:
 *
 * - the scheme is `wss` iff the base *starts with* `https`, so `https://host`
 *   yields `wss` regardless of the connection mode;
 * - the token is appended **raw**. `URLSearchParams` would percent-encode `+`
 *   and `=` in a base64url-ish JWT and the server would reject it, so this is
 *   deliberate string concatenation.
 *
 * Returns `null` when `relay` mode is asked for without an agent id.
 */
export function buildWebSocketUrl(spec: WebSocketUrlSpec): string | null {
  const base = trimTrailingSlashes(spec.base);
  const scheme = base.startsWith('https') ? 'wss' : 'ws';
  const host = stripScheme(base);

  let path = spec.path;
  if (spec.mode === 'relay') {
    if (spec.agentId === undefined || spec.agentId === null || spec.agentId === '') {
      return null;
    }
    path = mapToRelayPath(path, spec.agentId);
  }

  const url = `${scheme}://${host}/${path}`;
  const separator = url.includes('?') ? '&' : '?';
  return `${url}${separator}token=${spec.token}`;
}

export interface AgentRestBaseSpec {
  mode: ConnectionMode;
  agentUrl?: string | null;
  relayUrl?: string | null;
  agentId?: string | null;
}

/**
 * REST base for the agent axis: the agent itself in direct mode, the relay's
 * `/proxy/{agentId}` tunnel otherwise. Included for parity with the mobile
 * clients and the `rest_base_cases` golden table — the web client's own agent
 * client is built from the same rule in `src/api/clients.ts`.
 *
 * Unlike the WebSocket builders above, this one encodes the agent id itself, so
 * it matches `createAgentClient`'s `/proxy/${encodeURIComponent(agentId)}`
 * rather than quietly diverging from it. A deliberate web-side hardening over
 * `RelayRouting.swift`; the golden `rest_base_cases` ids are alphanumeric, so
 * the compared bytes are unchanged.
 */
export function agentRestBaseUrl(spec: AgentRestBaseSpec): string | null {
  if (spec.mode === 'direct') {
    return spec.agentUrl ?? null;
  }
  const relay = spec.relayUrl;
  if (relay === undefined || relay === null || relay === '') return null;
  if (spec.agentId === undefined || spec.agentId === null || spec.agentId === '') {
    return null;
  }
  return `${trimTrailingSlashes(relay)}/proxy/${encodeURIComponent(spec.agentId)}`;
}

import { DEVICE_PLATFORM, getDeviceId, getDeviceName } from '../auth/deviceIdentity';
import { relayClient } from './clients';
import { toApiError } from './errors';

export interface Agent {
  agentId: string;
  name: string;
  online: boolean;
  createdAt: string;
  lastSeenAt: string | null;
  agentVersion: string | null;
  agentProtocolVersion: number | null;
}

/**
 * Online agents first, then by name.
 *
 * Mobile has no equivalent — one install is bound to one agent — so the order
 * is a web decision: an offline agent's session list is unreachable, which
 * makes it the wrong thing to default a fresh browser to.
 */
function byOnlineThenName(a: Agent, b: Agent): number {
  if (a.online !== b.online) return a.online ? -1 : 1;
  return a.name.toLowerCase().localeCompare(b.name.toLowerCase());
}

export async function listAgents(): Promise<Agent[]> {
  const { data, error, response } = await relayClient.GET('/api/agents');
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return data
    .map((agent) => ({
      agentId: agent.agent_id,
      name: agent.name,
      online: agent.online,
      createdAt: agent.created_at,
      lastSeenAt: agent.last_seen_at,
      agentVersion: agent.agent_version ?? null,
      agentProtocolVersion: agent.agent_protocol_version ?? null,
    }))
    .sort(byOnlineThenName);
}

/**
 * Credentials the relay hands back exactly once, from a manual create or a
 * re-register. Nothing persists them — they go straight to the screen that
 * shows the operator what to paste into the agent host's `.env`.
 */
export interface AgentCredentials {
  agentId: string;
  name: string;
  token: string;
}

export async function createAgent(name: string): Promise<AgentCredentials> {
  const { data, error, response } = await relayClient.POST('/api/agents', {
    body: { name },
  });
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return { agentId: data.agent_id, name: data.name, token: data.token };
}

/**
 * Rotate an agent's credentials, invalidating the current token immediately.
 *
 * The response carries no name, and the list is deliberately not refetched:
 * nothing about the row changed, and both mobile clients leave it alone too.
 */
export async function reregisterAgent(
  agentId: string,
  name: string,
): Promise<AgentCredentials> {
  const { data, error, response } = await relayClient.POST(
    '/api/agents/{agent_id}/reregister',
    { params: { path: { agent_id: agentId } } },
  );
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return { agentId: data.agent_id, name, token: data.token };
}

export async function deleteAgent(agentId: string): Promise<void> {
  const { error, response } = await relayClient.DELETE('/api/agents/{agent_id}', {
    params: { path: { agent_id: agentId } },
  });
  if (error !== undefined) {
    throw toApiError(response, error);
  }
}

export interface PairingClaimResult {
  agentId: string;
  agentFriendlyName: string;
  /**
   * Tri-state, and `null` must never be collapsed to `false`: an older relay
   * omits the field entirely. It says whether the credential push reached the
   * agent's open pairing WebSocket or was staged for its next reconnect, and it
   * is a diagnostic that refines the timeout copy — never proof of pairing.
   * Only `agent.status_changed` with `online: true` is authoritative.
   */
  wsDelivered: boolean | null;
}

/**
 * Consume a pairing nonce for this account.
 *
 * No token comes back — the relay signs the credentials and pushes them to the
 * agent over its pairing WebSocket, so nothing secret ever reaches the browser.
 * The device fields are the same three `agentTokens.mint` sends, which is what
 * registers this browser on the devices screen alongside the phones.
 */
export async function claimPairing(
  nonce: string,
  trustKeyFp: string,
): Promise<PairingClaimResult> {
  const { data, error, response } = await relayClient.POST('/api/pairing/claim', {
    body: {
      nonce,
      trust_key_fp: trustKeyFp,
      mobile_device_id: getDeviceId(),
      mobile_device_name: getDeviceName(),
      platform: DEVICE_PLATFORM,
    },
  });
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return {
    agentId: data.agent_id,
    agentFriendlyName: data.agent_friendly_name,
    wsDelivered: data.ws_delivered ?? null,
  };
}

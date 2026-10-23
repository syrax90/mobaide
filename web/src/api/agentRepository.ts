import { DEVICE_PLATFORM, getDeviceId, getDeviceName } from '../auth/deviceIdentity';
import type { DeviceCodeBlockedReason } from '../routes/pair/pairCopy';
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
  /** The build that last connected can uninstall itself when removed. */
  supportsUninstall: boolean;
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
      supportsUninstall: agent.supports_uninstall ?? false,
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

/**
 * Remove an agent from the account. With `uninstall`, the agent also deletes
 * itself from its host: at once when online, otherwise on its next connect
 * within 30 days.
 */
export async function deleteAgent(agentId: string, uninstall = false): Promise<void> {
  const { error, response } = await relayClient.DELETE('/api/agents/{agent_id}', {
    params: {
      path: { agent_id: agentId },
      ...(uninstall ? { query: { uninstall: true } } : {}),
    },
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

export interface DeviceCodeMint {
  requestId: string;
  code: string;
  expiresAt: string;
  verificationCommand: string;
}

/**
 * Mint a short code for the operator to retype on the machine being paired.
 *
 * The reverse of {@link claimPairing}: nothing has to be copied *off* the agent
 * host, which is what makes it work over an SSH jump host or a VM console.
 * Minting supersedes this account's previous unredeemed code.
 */
export async function mintDeviceCode(): Promise<DeviceCodeMint> {
  const { data, error, response } = await relayClient.POST('/api/pairing/device-code', {});
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return {
    requestId: data.request_id,
    code: data.code,
    expiresAt: data.expires_at,
    verificationCommand: data.verification_command,
  };
}

export type DeviceCodeStatusKind =
  | 'pending'
  | 'redeemed'
  | 'confirmed'
  | 'canceled'
  | 'expired';

export interface DeviceCodeStatus {
  status: DeviceCodeStatusKind;
  expiresAt: string;
  /**
   * Reported by the machine that entered the code, over an endpoint that
   * authenticates nobody — so it identifies a host to a human who recognises
   * it, and proves nothing on its own.
   */
  hostname: string | null;
  os: string | null;
  isRepair: boolean;
  /**
   * Set only when the redeeming host claims an agent this account already
   * owns. Approving then moves that agent rather than adding one, so the
   * confirm screen has to say so.
   */
  repairAgentName: string | null;
  blockedReason: DeviceCodeBlockedReason | null;
}

export async function getDeviceCodeStatus(requestId: string): Promise<DeviceCodeStatus> {
  const { data, error, response } = await relayClient.GET(
    '/api/pairing/device-code/{request_id}',
    { params: { path: { request_id: requestId } } },
  );
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return {
    status: data.status,
    expiresAt: data.expires_at,
    hostname: data.hostname ?? null,
    os: data.os ?? null,
    isRepair: data.is_repair,
    repairAgentName: data.repair_agent_name ?? null,
    blockedReason: data.blocked_reason ?? null,
  };
}

/**
 * Approve a redeemed code, releasing credentials to the waiting agent.
 *
 * Sends the same device fields as {@link claimPairing} because the relay runs
 * the identical completion path: this browser has to land in the device
 * registry and in the nonce's `consumed_by` column either way.
 */
export async function confirmDeviceCode(requestId: string): Promise<PairingClaimResult> {
  const { data, error, response } = await relayClient.POST(
    '/api/pairing/device-code/{request_id}/confirm',
    {
      params: { path: { request_id: requestId } },
      body: {
        mobile_device_id: getDeviceId(),
        mobile_device_name: getDeviceName(),
        platform: DEVICE_PLATFORM,
      },
    },
  );
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return {
    agentId: data.agent_id,
    agentFriendlyName: data.agent_friendly_name,
    wsDelivered: data.ws_delivered ?? null,
  };
}

/** Abandon a pairing request and expire any nonce it handed out. */
export async function cancelDeviceCode(requestId: string): Promise<void> {
  const { error, response } = await relayClient.DELETE(
    '/api/pairing/device-code/{request_id}',
    { params: { path: { request_id: requestId } } },
  );
  if (error !== undefined) {
    throw toApiError(response, error);
  }
}

export interface TrustPin {
  relayUrl: string;
  relayTrustKeyFp: string;
  /** `null` until this account completes its first pairing. */
  accountTrustKeyFp: string | null;
}

/**
 * Read the relay's current signing fingerprint and this account's recorded pin.
 *
 * Display and comparison only. It deliberately does not feed `pinTrustKey`: a
 * local pin records what the user personally vouched for, and a value the relay
 * hands out is exactly what a compromised relay would forge. Enforcement of the
 * account pin lives on the server, where a wiped browser cannot lose it.
 */
export async function fetchTrustPin(): Promise<TrustPin> {
  const { data, error, response } = await relayClient.GET('/api/pairing/trust-pin');
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return {
    relayUrl: data.relay_url,
    relayTrustKeyFp: data.relay_trust_key_fp,
    accountTrustKeyFp: data.account_trust_key_fp ?? null,
  };
}

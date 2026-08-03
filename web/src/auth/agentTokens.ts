/**
 * Per-agent `mobile_access` tokens — the second auth axis.
 *
 * These are minted from the user session (`POST /api/agents/{id}/token`) and
 * rotated with the opaque refresh token (`POST /api/mobile/refresh`). They are
 * always re-mintable from a live user session, which is what makes it safe to
 * keep them in `localStorage` while the user JWT stays in `sessionStorage`:
 * sharing them across tabs is what gives the cross-tab lock something real to
 * protect, since rotating a refresh token in one tab invalidates every other
 * tab's copy.
 *
 * No screen consumes this yet — the agent surface arrives with WEB-08 — but
 * `createAgentClient` already reads through `getAgentToken`, so the axis is
 * wired end to end here.
 */
import { relayClient } from '../api/clients';
import { DEVICE_PLATFORM, getDeviceId, getDeviceName } from './deviceIdentity';
import { readSession } from './sessionTransport';
import { singleFlight } from './singleFlight';
import { isAccessTokenExpiringSoon, REFRESH_TIMEOUT_MS } from './userTokens';

const STORAGE_KEY = 'mobaide-agent-tokens';

interface AgentTokenPair {
  accessToken: string;
  refreshToken: string;
}

type AgentTokenMap = Record<string, AgentTokenPair>;

function isTokenPair(value: unknown): value is AgentTokenPair {
  if (typeof value !== 'object' || value === null) return false;
  const pair = value as Record<string, unknown>;
  return (
    typeof pair.accessToken === 'string' && typeof pair.refreshToken === 'string'
  );
}

/**
 * In-memory mirror, and the sole source when storage is unavailable.
 *
 * `localStorage` throws in a partitioned or storage-blocked context. Without
 * this the axis would be dead for the page's lifetime rather than merely
 * unshared: `getAgentToken` is read synchronously by the bearer middleware, so
 * a read that returns nothing means no `Authorization` header is ever
 * attached, and no amount of re-minting helps because the write is lost too.
 * Same fallback shape as `deviceIdentity`'s cached id.
 */
let memoryTokens: AgentTokenMap = {};

function readStored(): AgentTokenMap {
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(STORAGE_KEY);
  } catch {
    return {};
  }
  if (raw === null) return {};
  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== 'object' || parsed === null) return {};
    const result: AgentTokenMap = {};
    for (const [agentId, pair] of Object.entries(parsed)) {
      if (isTokenPair(pair)) result[agentId] = pair;
    }
    return result;
  } catch {
    return {};
  }
}

function readAll(): AgentTokenMap {
  // Memory wins: it holds this tab's most recent writes, which is what matters
  // when a rotation could not be persisted.
  return { ...readStored(), ...memoryTokens };
}

function writeAll(map: AgentTokenMap): void {
  memoryTokens = map;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
  } catch {
    // Storage denied — the memory mirror above keeps the axis working for this
    // page, at the cost of not sharing it with other tabs.
  }
}

/** Synchronous read for the bearer middleware. Never triggers a network call. */
export function getAgentToken(agentId: string): string | null {
  return readAll()[agentId]?.accessToken ?? null;
}

function store(agentId: string, pair: AgentTokenPair): void {
  const map = readAll();
  map[agentId] = pair;
  writeAll(map);
}

/**
 * Drop every agent token. Called on sign-out and on a boot that finds no user
 * session — these are useless without one, and leaving them in `localStorage`
 * would strand a credential on disk past the session that authorised it.
 */
export function clearAgentTokens(): void {
  // Memory first, so a storage failure still drops the credentials this page
  // is actually using.
  memoryTokens = {};
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Nothing further to do — a storage failure here cannot be surfaced
    // usefully, and the live copy is already gone.
  }
}

/**
 * Mint a fresh pair for an agent.
 *
 * Goes through `relayClient` like every other user-JWT call in this codebase
 * (`authRepository`, `deviceRepository`) rather than hand-rolling the bearer:
 * the path is in the generated schema, so the response is typed, and a 401
 * reaches the session-teardown middleware — which is right, because this call
 * authenticates with the *user* token. Android's mint goes through
 * `CentralApi` for exactly the same reason; only the refresh calls below
 * bypass the client, mirroring its dedicated interceptor-free refresh client.
 */
async function mint(agentId: string): Promise<AgentTokenPair | null> {
  if (readSession() === null) return null;
  const { data, error } = await relayClient.POST(
    '/api/agents/{agent_id}/token',
    {
      params: { path: { agent_id: agentId } },
      body: {
        mobile_device_id: getDeviceId(),
        mobile_device_name: getDeviceName(),
        platform: DEVICE_PLATFORM,
      },
    },
  );
  if (error !== undefined || data === undefined) return null;
  return {
    accessToken: data.access_token,
    refreshToken: data.refresh_token,
  };
}

/** Narrow a `MobileTokenResponse` body from an untyped refresh round trip. */
async function readTokenPair(
  response: Response,
): Promise<AgentTokenPair | null> {
  if (!response.ok) return null;
  const body: unknown = await response.json();
  if (typeof body !== 'object' || body === null) return null;
  const parsed = body as Record<string, unknown>;
  if (
    typeof parsed.access_token !== 'string' ||
    typeof parsed.refresh_token !== 'string'
  ) {
    return null;
  }
  return {
    accessToken: parsed.access_token,
    refreshToken: parsed.refresh_token,
  };
}

async function rotate(refreshToken: string): Promise<AgentTokenPair | null> {
  try {
    const response = await fetch('/api/mobile/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
      // Bounded so a stalled connection cannot hold the cross-tab lock.
      signal: AbortSignal.timeout(REFRESH_TIMEOUT_MS),
    });
    return await readTokenPair(response);
  } catch {
    // Including the abort: the caller falls back to a fresh mint.
    return null;
  }
}

/**
 * Return a usable access token for an agent, minting or rotating as needed.
 *
 * Single-flight is keyed per agent so two agents can be prepared in parallel
 * while concurrent requests for the *same* agent collapse into one round trip.
 * A failed rotation falls back to a fresh mint rather than surfacing an error:
 * the refresh token may simply have been consumed by another tab, or revoked
 * with the device, and the user session is still authority enough to re-mint.
 */
export function ensureAgentToken(agentId: string): Promise<string | null> {
  return singleFlight<string | null>(
    `agent-token-${agentId}`,
    async () => {
      const existing = readAll()[agentId];
      if (existing !== undefined) {
        const rotated = await rotate(existing.refreshToken);
        if (rotated !== null) {
          store(agentId, rotated);
          return rotated.accessToken;
        }
      }
      const minted = await mint(agentId);
      if (minted === null) return null;
      store(agentId, minted);
      return minted.accessToken;
    },
    {
      recheck: () => {
        const existing = readAll()[agentId];
        if (existing === undefined) return null;
        return isAccessTokenExpiringSoon(existing.accessToken)
          ? null
          : existing.accessToken;
      },
    },
  );
}

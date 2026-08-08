import type { BrowserContext, Page } from '@playwright/test';

// CI has no relay, so every server response is stubbed at the network layer.
export const SERVER_INFO = {
  name: 'central_server',
  server_version: '0.9.9',
  protocol_versions: [6, 7],
  min_client_version: 6,
  capabilities: {},
  google_client_id: null,
  apple_web_client_id: null,
  latest_agent_version: null,
};

export const AGENTS = [
  {
    agent_id: 'agent-00000001',
    name: 'Workstation',
    online: true,
    created_at: '2026-07-01T10:00:00+00:00',
    last_seen_at: '2026-08-01T10:00:00+00:00',
    agent_version: '0.9.9',
    agent_protocol_version: 7,
  },
];

/** `CreateAgentResponse` — the manual-add path, which returns a token once. */
export const CREATED_AGENT = {
  agent_id: 'agent-00000002',
  name: 'Laptop',
  token: 'agent-token-shown-once',
};

/** The agent a pairing claim attaches to the account. */
export const PENDING_AGENT = {
  agent_id: 'agent-00000003',
  name: 'Paired host',
  online: false,
  created_at: '2026-08-04T10:00:00+00:00',
  last_seen_at: null,
  agent_version: '0.9.9',
  agent_protocol_version: 7,
};

/** `PairingClaimResponse`. `ws_delivered` is tri-state; this is the `true` arm. */
export const PAIRING_CLAIM = {
  agent_id: PENDING_AGENT.agent_id,
  agent_friendly_name: PENDING_AGENT.name,
  ws_delivered: true,
};

export const SESSION = {
  access_token: 'access-token-1',
  refresh_token: 'refresh-token-1',
  token_type: 'bearer',
  user_id: 'user-00000001',
  email: 'user@example.com',
  role: 'viewer',
  status: 'ok',
  scheduled_deletion_at: null,
};

export const THIS_DEVICE_ID = '11111111-2222-4333-8444-555555555555';

export const DEVICES = [
  {
    device_id: THIS_DEVICE_ID,
    name: 'Chrome on macOS',
    created_at: '2026-07-01T10:00:00+00:00',
    last_seen_at: '2026-08-01T10:00:00+00:00',
    platform: 'web',
  },
  {
    device_id: '22222222-2222-4333-8444-555555555555',
    name: 'Personal iPhone',
    created_at: '2026-06-01T10:00:00+00:00',
    last_seen_at: null,
    platform: 'ios',
  },
  {
    device_id: '33333333-2222-4333-8444-555555555555',
    name: 'Pixel 8',
    created_at: '2026-05-01T10:00:00+00:00',
    last_seen_at: null,
    platform: 'android',
  },
  {
    device_id: '44444444-2222-4333-8444-555555555555',
    name: 'Old tablet',
    created_at: '2026-01-01T10:00:00+00:00',
    last_seen_at: null,
    platform: null,
  },
];

export function json(body: unknown) {
  return {
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  };
}

export interface StubRelayOptions {
  /**
   * Start the agent list with `PENDING_AGENT` offline and absent, the way a
   * relay looks before a pairing code is claimed. The claim handler adds it,
   * still offline; `bringPendingAgentOnline` is what flips it — so a test
   * cannot accidentally pass by reading the claim's 200 as proof of pairing.
   */
  pendingPairing?: boolean;
}

/**
 * Per-call, never module-level: two tests in one file share this module, and a
 * mutable stub state that outlived a test would make the pairing spec's result
 * depend on execution order.
 */
export interface RelayStub {
  bringPendingAgentOnline: () => void;
}

export async function stubRelay(
  target: Page | BrowserContext,
  version: unknown = SERVER_INFO,
  options: StubRelayOptions = {},
): Promise<RelayStub> {
  let claimed = false;
  let pendingOnline = false;
  const agentsSnapshot = () => {
    if (options.pendingPairing !== true) return AGENTS;
    if (!claimed) return [];
    return [{ ...PENDING_AGENT, online: pendingOnline }];
  };

  await target.route('**/api/version', (route) => route.fulfill(json(version)));
  await target.route('**/api/auth/login', (route) => route.fulfill(json(SESSION)));
  await target.route('**/api/devices', (route) => route.fulfill(json(DEVICES)));
  // `SESSION.access_token` is not a real JWT, so the client cannot read an
  // `exp` from it and treats it as due for renewal before every request.
  // Stubbing the rotation keeps that off the network instead of leaving
  // ECONNREFUSED noise in the CI log.
  await target.route('**/api/auth/refresh', (route) => route.fulfill(json(SESSION)));
  // The sessions list is the index route, so a signed-in visitor hits these on
  // arrival. Without the agent-token mint the client retries a 404 and leaves
  // noise in the log, for the same reason the refresh stub exists.
  //
  // Method-aware: `/api/agents` is both the list (GET) and manual create
  // (POST). A single handler would answer a create with an array, which the
  // repository reads as `data.agent_id === undefined` — a fixture that
  // certifies the very bug it exists to cover.
  await target.route('**/api/agents', (route) => {
    if (route.request().method() === 'POST') {
      return route.fulfill({
        ...json(CREATED_AGENT),
        status: 201,
      });
    }
    return route.fulfill(json(agentsSnapshot()));
  });
  await target.route('**/api/pairing/claim', (route) => {
    // The claim only proves the relay persisted credentials. The agent coming
    // online is a separate fact the relay learns later, so the row appears
    // offline here and only `bringPendingAgentOnline` flips it.
    claimed = true;
    return route.fulfill(json(PAIRING_CLAIM));
  });
  // Must be the real `MobileTokenResponse` shape: `mint()` reads `access_token`
  // and `refresh_token`, so an invented `{token}` leaves both undefined, fails
  // the token-pair guard, and silently runs the agent axis with no bearer at
  // all — a fixture that would certify the very bug it exists to cover.
  await target.route('**/api/agents/*/token', (route) =>
    route.fulfill(
      json({
        access_token: 'agent-access-1',
        refresh_token: 'agent-refresh-1',
        token_type: 'bearer',
      }),
    ),
  );
  await target.route('**/api/custom-agent-types', (route) => route.fulfill(json([])));
  await target.route('**/proxy/*/api/sessions', (route) => route.fulfill(json([])));

  return {
    bringPendingAgentOnline: () => {
      pendingOnline = true;
    },
  };
}

export async function signIn(page: Page): Promise<void> {
  await page.getByLabel('Email').fill('user@example.com');
  // "Password" alone also matches the reveal button's aria-label.
  await page.getByLabel('Password', { exact: true }).fill('hunter22');
  await page.getByRole('button', { name: 'Sign In' }).click();
}

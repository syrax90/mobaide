import type { BrowserContext, Page } from '@playwright/test';

// CI has no relay, so every server response is stubbed at the network layer.
export const SERVER_INFO = {
  name: 'central_server',
  server_version: '0.9.9',
  protocol_versions: [6, 7],
  min_client_version: 6,
  capabilities: {},
  google_client_id: null,
  latest_agent_version: null,
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

export async function stubRelay(
  target: Page | BrowserContext,
  version: unknown = SERVER_INFO,
): Promise<void> {
  await target.route('**/api/version', (route) => route.fulfill(json(version)));
  await target.route('**/api/auth/login', (route) => route.fulfill(json(SESSION)));
  await target.route('**/api/devices', (route) => route.fulfill(json(DEVICES)));
  // `SESSION.access_token` is not a real JWT, so the client cannot read an
  // `exp` from it and treats it as due for renewal before every request.
  // Stubbing the rotation keeps that off the network instead of leaving
  // ECONNREFUSED noise in the CI log.
  await target.route('**/api/auth/refresh', (route) => route.fulfill(json(SESSION)));
}

export async function signIn(page: Page): Promise<void> {
  await page.getByLabel('Email').fill('user@example.com');
  // "Password" alone also matches the reveal button's aria-label.
  await page.getByLabel('Password', { exact: true }).fill('hunter22');
  await page.getByRole('button', { name: 'Sign In' }).click();
}

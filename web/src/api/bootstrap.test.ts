import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  APP_PROTOCOL_VERSION,
  checkVersionGate,
  fetchServerInfo,
  type ServerInfo,
} from './bootstrap';

function serverInfo(overrides: Partial<ServerInfo> = {}): ServerInfo {
  return {
    serverVersion: '1.2.3',
    protocolVersions: [6, 7],
    minClientVersion: 6,
    googleClientId: null,
    appleWebClientId: null,
    ...overrides,
  };
}

function stubVersionProbe(body: Record<string, unknown>): void {
  vi.stubGlobal('fetch', () =>
    Promise.resolve(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    ),
  );
}

const VERSION_BODY = {
  server_version: '1.2.3',
  protocol_versions: [7],
  min_client_version: 7,
};

describe('fetchServerInfo', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('reads each provider id out of the one anonymous probe', async () => {
    stubVersionProbe({
      ...VERSION_BODY,
      google_client_id: 'web-client.apps.googleusercontent.com',
      apple_web_client_id: 'com.mobaide.app.web',
    });

    const info = await fetchServerInfo();

    // The SPA is static files with no build-time config channel, so this call
    // is the only place either id can come from.
    expect(info.googleClientId).toBe('web-client.apps.googleusercontent.com');
    expect(info.appleWebClientId).toBe('com.mobaide.app.web');
  });

  it('reports an unconfigured provider as null, never undefined', async () => {
    // A server older than this feature omits the key outright.
    stubVersionProbe(VERSION_BODY);

    const info = await fetchServerInfo();

    // `AppleSignInButton` hides itself on `=== null`; leaving it undefined
    // would mount a button that initialises the SDK with no client id.
    expect(info.appleWebClientId).toBeNull();
    expect(info.googleClientId).toBeNull();
  });

  it('maps an explicit null the same way', async () => {
    stubVersionProbe({ ...VERSION_BODY, apple_web_client_id: null });

    expect((await fetchServerInfo()).appleWebClientId).toBeNull();
  });
});

describe('checkVersionGate', () => {
  it('passes when the client version is offered and at or above the minimum', () => {
    expect(
      checkVersionGate(
        serverInfo({ protocolVersions: [6, 7, APP_PROTOCOL_VERSION], minClientVersion: 6 }),
      ),
    ).toBeNull();
  });

  it('passes when the client is exactly the minimum and the only protocol', () => {
    expect(
      checkVersionGate(
        serverInfo({
          protocolVersions: [APP_PROTOCOL_VERSION],
          minClientVersion: APP_PROTOCOL_VERSION,
        }),
      ),
    ).toBeNull();
  });

  it('fails when the server has moved past this client', () => {
    const nextVersion = APP_PROTOCOL_VERSION + 1;
    const gate = checkVersionGate(
      serverInfo({
        protocolVersions: [nextVersion, nextVersion + 1],
        minClientVersion: nextVersion,
      }),
    );

    expect(gate).not.toBeNull();
    expect(gate?.minClientVersion).toBe(nextVersion);
    expect(gate?.reason).toContain(`v${String(APP_PROTOCOL_VERSION)}`);
  });

  it('fails when the version is dropped from the set despite a lower minimum', () => {
    // Set membership, not a range: min 6 admits this client, but the server
    // no longer speaks this client's version, so the handshake would still
    // fail.
    expect(
      checkVersionGate(
        serverInfo({ protocolVersions: [6, APP_PROTOCOL_VERSION + 1], minClientVersion: 6 }),
      ),
    ).not.toBeNull();
  });

  it('fails when the server advertises no protocols at all', () => {
    expect(
      checkVersionGate(serverInfo({ protocolVersions: [], minClientVersion: 0 })),
    ).not.toBeNull();
  });
});

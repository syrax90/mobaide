import { describe, expect, it } from 'vitest';
import { APP_PROTOCOL_VERSION, checkVersionGate, type ServerInfo } from './bootstrap';

function serverInfo(overrides: Partial<ServerInfo> = {}): ServerInfo {
  return {
    serverVersion: '1.2.3',
    protocolVersions: [6, 7],
    minClientVersion: 6,
    googleClientId: null,
    ...overrides,
  };
}

describe('checkVersionGate', () => {
  it('passes when the client version is offered and at or above the minimum', () => {
    expect(
      checkVersionGate(serverInfo({ protocolVersions: [6, 7], minClientVersion: 6 })),
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
    const gate = checkVersionGate(
      serverInfo({ protocolVersions: [8, 9], minClientVersion: 8 }),
    );

    expect(gate).not.toBeNull();
    expect(gate?.minClientVersion).toBe(8);
    expect(gate?.reason).toContain(`v${String(APP_PROTOCOL_VERSION)}`);
  });

  it('fails when the version is dropped from the set despite a lower minimum', () => {
    // Set membership, not a range: min 6 admits this client, but the server
    // no longer speaks v7, so the handshake would still fail.
    expect(
      checkVersionGate(serverInfo({ protocolVersions: [6, 8], minClientVersion: 6 })),
    ).not.toBeNull();
  });

  it('fails when the server advertises no protocols at all', () => {
    expect(
      checkVersionGate(serverInfo({ protocolVersions: [], minClientVersion: 0 })),
    ).not.toBeNull();
  });
});

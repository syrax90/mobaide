// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { readProtocolGolden } from './__fixtures__/load';
import { isSendableCloseCode, WS_CLOSE } from './closeCodes';

interface ProtocolGolden {
  protocol_version: number;
  min_client_protocol_version: number;
  supported_protocol_versions: number[];
  close_codes: Record<string, number>;
}

const golden = readProtocolGolden<ProtocolGolden>();

describe('close codes against tests/protocol_golden.json', () => {
  // The Python suite only asserts `protocol_incompatible` out of this block, so
  // until CLA-218 lands this is the only check on the other four.
  it('matches every code the golden file names', () => {
    expect(golden.close_codes).toEqual({
      auth_failed: WS_CLOSE.authFailed,
      protocol_incompatible: WS_CLOSE.protocolIncompatible,
      admin_revoked: WS_CLOSE.adminRevoked,
      admin_force_disconnect: WS_CLOSE.adminForceDisconnect,
      agent_transient_offline: WS_CLOSE.agentTransientOffline,
    });
  });

  it('agrees with the protocol version this client speaks', async () => {
    const { APP_PROTOCOL_VERSION } = await import('../api/bootstrap');
    expect(golden.supported_protocol_versions).toContain(APP_PROTOCOL_VERSION);
    expect(APP_PROTOCOL_VERSION).toBeGreaterThanOrEqual(
      golden.min_client_protocol_version,
    );
  });
});

describe('client-sendable close codes', () => {
  it('accepts only what the WebSocket API allows a client to send', () => {
    expect(isSendableCloseCode(WS_CLOSE.normal)).toBe(true);
    expect(isSendableCloseCode(3000)).toBe(true);
    expect(isSendableCloseCode(4999)).toBe(true);
    // Everything else throws InvalidAccessError in a browser.
    expect(isSendableCloseCode(1001)).toBe(false);
    expect(isSendableCloseCode(1006)).toBe(false);
    expect(isSendableCloseCode(5000)).toBe(false);
  });
});

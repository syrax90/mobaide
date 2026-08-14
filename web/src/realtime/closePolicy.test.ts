import { describe, expect, it } from 'vitest';
import { WS_CLOSE } from '../protocol/closeCodes';
import { closePolicyFor } from './closePolicy';

describe('closePolicyFor on the user axis', () => {
  it('ends the session when the credential is rejected', () => {
    expect(closePolicyFor(WS_CLOSE.authFailed, 'user', '')).toEqual({
      reconnect: false,
      terminal: { kind: 'tokenRejected' },
      clearAgentToken: false,
    });
  });

  it('surfaces the server reason for an incompatible protocol', () => {
    expect(closePolicyFor(WS_CLOSE.protocolIncompatible, 'user', 'need proto 8')).toEqual({
      reconnect: false,
      terminal: { kind: 'incompatibleServer', reason: 'need proto 8' },
      clearAgentToken: false,
    });
  });

  it('substitutes a reason when the close frame carries none', () => {
    // No client renders the reason (see `channelRegistry.ts`), so the fallback is
    // for the state model and the debugger: a blank string would make the
    // terminal outcome indistinguishable from one that never carried a reason.
    expect(closePolicyFor(WS_CLOSE.protocolIncompatible, 'user', '')).toEqual({
      reconnect: false,
      terminal: { kind: 'incompatibleServer', reason: 'Server protocol incompatible' },
      clearAgentToken: false,
    });
  });

  it('reports an administratively removed account', () => {
    expect(closePolicyFor(WS_CLOSE.adminRevoked, 'user', 'gone')).toEqual({
      reconnect: false,
      terminal: { kind: 'accountRemoved' },
      clearAgentToken: false,
    });
  });
});

describe('closePolicyFor on the agent axis', () => {
  it('drops only the cached agent token when the credential is rejected', () => {
    // An agent token is always re-mintable, so a 4001 here must not surface as
    // a terminal outcome that would tear the user session down.
    expect(closePolicyFor(WS_CLOSE.authFailed, 'agent', '')).toEqual({
      reconnect: false,
      terminal: null,
      clearAgentToken: true,
    });
  });

  it('stops quietly on codes that are terminal only for the user session', () => {
    const stop = { reconnect: false, terminal: null, clearAgentToken: false };

    expect(closePolicyFor(WS_CLOSE.protocolIncompatible, 'agent', 'need proto 8')).toEqual(stop);
    expect(closePolicyFor(WS_CLOSE.adminRevoked, 'agent', '')).toEqual(stop);
  });

  it('re-mints and reconnects when the token is not valid for this agent', () => {
    expect(closePolicyFor(WS_CLOSE.tokenNotValidForAgent, 'agent', '')).toEqual({
      reconnect: true,
      terminal: null,
      clearAgentToken: true,
    });
  });
});

describe('closePolicyFor recoverable closes', () => {
  const recoverable = [
    WS_CLOSE.normal,
    WS_CLOSE.agentBusy,
    WS_CLOSE.adminForceDisconnect,
    WS_CLOSE.agentTransientOffline,
    WS_CLOSE.tokenNotValidForAgent,
    1006,
    4999,
  ];

  it('keeps the backoff loop running on both axes', () => {
    // 1006 is the important row: a browser reports every pre-accept rejection
    // that way, so anything but "reconnect" here would strand the channel.
    for (const code of recoverable) {
      expect(closePolicyFor(code, 'user', '')).toEqual({
        reconnect: true,
        terminal: null,
        clearAgentToken: false,
      });
      expect(closePolicyFor(code, 'agent', '').reconnect).toBe(true);
    }
  });
});

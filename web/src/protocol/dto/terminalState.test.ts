// @vitest-environment node
import { describe, expect, it } from 'vitest';
import {
  decodeTerminalState,
  isSessionRunning,
  TERMINAL_STATUS_RUNNING,
  type TerminalState,
} from './terminalState';

describe('decodeTerminalState', () => {
  it('decodes the running frame the agent sends on every socket open', () => {
    expect(decodeTerminalState('{"status":"running","pid":4242}')).toEqual({
      status: 'running',
      pid: 4242,
    });
  });

  it('treats an explicit null the same as an absent key', () => {
    const state = decodeTerminalState('{"status":"starting","pid":null,"exit_code":null}');

    expect(state?.pid).toBeUndefined();
    expect(state?.exit_code).toBeUndefined();
  });

  it('still decodes a status this client has never heard of', () => {
    // A newer agent inventing a sixth `_STATE_MAP` value must not make the
    // frame undecodable — the only behavioural read is the `running` compare.
    expect(decodeTerminalState('{"status":"hibernating"}')?.status).toBe('hibernating');
  });

  it('returns null rather than throwing on a frame it cannot use', () => {
    expect(decodeTerminalState('not json')).toBeNull();
    expect(decodeTerminalState('')).toBeNull();
    expect(decodeTerminalState('{"pid":1}')).toBeNull();
    expect(decodeTerminalState('{"status":7}')).toBeNull();
  });
});

describe('isSessionRunning', () => {
  const state = (status: string): TerminalState => ({
    status,
    pid: undefined,
    exit_code: undefined,
  });

  it('gates sends on the running status alone', () => {
    expect(isSessionRunning(state(TERMINAL_STATUS_RUNNING))).toBe(true);
    expect(isSessionRunning(state('starting'))).toBe(false);
    expect(isSessionRunning(state('crashed'))).toBe(false);
    // No frame has arrived yet, or the last one did not decode.
    expect(isSessionRunning(null)).toBe(false);
  });
});

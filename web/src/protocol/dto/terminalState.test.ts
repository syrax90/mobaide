// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { PANE_CLIENT_ID } from '../paneClientId';
import {
  decodeTerminalState,
  isPaneControlledElsewhere,
  isPaneController,
  isSessionRunning,
  remoteGrid,
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

describe('remoteGrid', () => {
  const grid = (json: string) => remoteGrid(decodeTerminalState(json));

  it('reads the grid of a Herdr pane in watch mode', () => {
    expect(grid('{"status":"running","pane":{"mode":"watch","cols":84,"rows":47}}')).toEqual({
      cols: 84,
      rows: 47,
    });
  });

  describe('a pane one client controls', () => {
    const controlled = (controller: string) =>
      decodeTerminalState(
        `{"status":"running","pane":{"mode":"control","controller":"${controller}","cols":40,"rows":20}}`,
      );

    it('dictates the controller\'s grid to every other client', () => {
      // A size from a watcher would resize the pane under the client that controls it.
      const state = controlled('phone');

      expect(remoteGrid(state, 'tablet')).toEqual({ cols: 40, rows: 20 });
      expect(isPaneControlledElsewhere(state, 'tablet')).toBe(true);
      expect(isPaneController(state, 'tablet')).toBe(false);
    });

    it('leaves the size to the client the block names', () => {
      // A dictated grid here would stop the resize frames that control exists for.
      const state = controlled('phone');

      expect(remoteGrid(state, 'phone')).toBeNull();
      expect(isPaneController(state, 'phone')).toBe(true);
      expect(isPaneControlledElsewhere(state, 'phone')).toBe(false);
    });

    it('knows this tab by the id it sends with "Take control"', () => {
      expect(remoteGrid(controlled(PANE_CLIENT_ID))).toBeNull();
      expect(remoteGrid(controlled('another-tab'))).toEqual({ cols: 40, rows: 20 });
    });
  });

  it('leaves the size to this client for a session that is not a pane', () => {
    expect(grid('{"status":"running"}')).toBeNull();
    expect(grid('{"status":"running","pane":null}')).toBeNull();
    expect(remoteGrid(null)).toBeNull();
  });

  it.each([
    '{"mode":"watch"}',
    '{"mode":"watch","cols":84}',
    '{"mode":"watch","cols":84,"rows":null}',
    '{"mode":"watch","cols":0,"rows":47}',
    '{"mode":"watch","cols":84,"rows":-1}',
    '{"mode":"watch","cols":84.5,"rows":47}',
    '{"mode":"observe","cols":84,"rows":47}',
  ])('has no grid for a block it cannot draw: %s', (pane) => {
    // xterm throws on a resize to a grid that is not a positive integer.
    expect(grid(`{"status":"running","pane":${pane}}`)).toBeNull();
  });
});

import { describe, expect, it } from 'vitest';
import type { TerminalInboundFrame } from '../protocol/terminalCodec';
import {
  CLEAR_BEFORE_REPLAY,
  initialReplayState,
  reduceDisconnected,
  reduceInboundFrame,
  type ReplayEffect,
  type ReplayState,
} from './terminalReplay';

const output = (...bytes: number[]): TerminalInboundFrame => ({
  kind: 'output',
  payload: Uint8Array.from(bytes),
});

const LIVE: ReplayState = { connection: 'live', inReplay: false };

const kinds = (effects: ReplayEffect[]): string[] =>
  effects.map((effect) => effect.kind);

describe('CLEAR_BEFORE_REPLAY', () => {
  it('is the eleven bytes both mobile clients send', () => {
    // `ESC[H ESC[2J ESC[3J` — cursor home, erase display, erase scrollback.
    // Byte-identical to Android `TerminalViewModel.CLEAR_BEFORE_REPLAY` and
    // iOS `clearBeforeReplay`; a divergence here shows up as replayed output
    // stacked on top of pre-drop history.
    expect([...CLEAR_BEFORE_REPLAY]).toEqual([
      0x1b, 0x5b, 0x48, 0x1b, 0x5b, 0x32, 0x4a, 0x1b, 0x5b, 0x33, 0x4a,
    ]);
  });
});

describe('reduceInboundFrame', () => {
  it('goes live before it wipes, on a replay start over a fresh socket', () => {
    // The upgrade precedes the decode, so the cached grid size is re-asserted
    // even on the frame that moves the screen straight into replay.
    const { state, effects } = reduceInboundFrame(initialReplayState(), {
      kind: 'replayStart',
    });

    expect(kinds(effects)).toEqual(['enterLive', 'wipe']);
    expect(state).toEqual({ connection: 'replaying', inReplay: true });
  });

  it('upgrades a connecting channel even when the frame did not decode', () => {
    const { state, effects } = reduceInboundFrame(initialReplayState(), null);

    expect(kinds(effects)).toEqual(['enterLive']);
    expect(state).toEqual(LIVE);
  });

  it('does not re-announce live for later frames on an open channel', () => {
    const { state, effects } = reduceInboundFrame(LIVE, output(0x68, 0x69));

    expect(effects).toEqual([{ kind: 'write', payload: Uint8Array.from([0x68, 0x69]) }]);
    expect(state).toEqual(LIVE);
  });

  it('writes nothing for the empty output frame an empty ring produces', () => {
    // The agent always emits at least one `o` inside the span, even with
    // nothing to replay.
    const { state, effects } = reduceInboundFrame(
      { connection: 'replaying', inReplay: true },
      output(),
    );

    expect(effects).toEqual([]);
    expect(state).toEqual({ connection: 'replaying', inReplay: true });
  });

  it('hands the raw state json to the caller', () => {
    const { effects } = reduceInboundFrame(LIVE, {
      kind: 'state',
      json: '{"status":"running"}',
    });

    expect(effects).toEqual([{ kind: 'state', json: '{"status":"running"}' }]);
  });

  it('ignores a replay end that closes no span', () => {
    const { state, effects } = reduceInboundFrame(LIVE, { kind: 'replayEnd' });

    expect(effects).toEqual([]);
    expect(state).toEqual(LIVE);
  });

  it('closes a real span and re-enters live', () => {
    const { state, effects } = reduceInboundFrame(
      { connection: 'replaying', inReplay: true },
      { kind: 'replayEnd' },
    );

    expect(kinds(effects)).toEqual(['replayFinished', 'enterLive']);
    expect(state).toEqual(LIVE);
  });

  it('walks the whole agent handshake', () => {
    // state -> 'b' -> N x 'o' -> 'e' -> live frames, run on every socket open.
    const seen: string[] = [];
    let state = initialReplayState();

    for (const frame of [
      { kind: 'state', json: '{"status":"running"}' } as const,
      { kind: 'replayStart' } as const,
      output(0x61),
      output(0x62),
      { kind: 'replayEnd' } as const,
      output(0x63),
    ]) {
      const transition = reduceInboundFrame(state, frame);
      state = transition.state;
      seen.push(...kinds(transition.effects));
    }

    expect(seen).toEqual([
      'enterLive',
      'state',
      'wipe',
      'write',
      'write',
      'replayFinished',
      'enterLive',
      'write',
    ]);
    expect(state).toEqual(LIVE);
  });
});

describe('reduceDisconnected', () => {
  it('leaves any open replay span behind', () => {
    // A socket that dies mid-replay would otherwise let the next cycle's `e`
    // match a span that never finished and clear the overlay early.
    expect(reduceDisconnected()).toEqual({ connection: 'connecting', inReplay: false });
  });
});

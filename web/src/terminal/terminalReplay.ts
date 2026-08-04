/**
 * The replay state machine, a pure mirror of Android
 * `TerminalViewModel.handleBinary` and iOS `TerminalSessionModel.handleBinary`.
 *
 * The agent runs the handshake on **every** socket open, not only the first
 * (`mobaide_agent/mobaide/agent/ws/session_io.py`):
 *
 *     state → 'b' → N × 'o'(≤64 KiB chunk) → 'e' → live frames…
 *
 * `N >= 1` always: an empty ring still emits one empty `'o'`, so a client can
 * rely on at least one output frame inside the span.
 *
 * Three details are load-bearing and all three come from the mobile clients:
 *
 * 1. **Any inbound binary upgrades a reconnecting channel to live, before the
 *    frame is decoded** — including the `'b'` that is about to move it straight
 *    on to `replaying`. That is where the second cached-size re-assert happens.
 * 2. **The wipe is bytes in the write stream, not `Terminal.reset()`.** `reset`
 *    executes immediately and would jump ahead of writes already queued, and it
 *    clears DECSET modes the program set. `ESC[H ESC[2J ESC[3J` preserves
 *    ordering, and the `ESC[3J` is what stops the replay stacking on top of
 *    pre-drop history.
 * 3. **`'e'` is guarded on `inReplay`** so a stray end marker is a no-op.
 */
import type { TerminalInboundFrame } from '../protocol/terminalCodec';

/**
 * `ESC[H` (cursor home) + `ESC[2J` (erase display) + `ESC[3J` (erase
 * scrollback) — byte-identical to Android `CLEAR_BEFORE_REPLAY` and iOS
 * `clearBeforeReplay`.
 */
export const CLEAR_BEFORE_REPLAY = Uint8Array.from([
  0x1b, 0x5b, 0x48, 0x1b, 0x5b, 0x32, 0x4a, 0x1b, 0x5b, 0x33, 0x4a,
]);

/**
 * Screen-level connection state — Android's three-value enum exactly.
 *
 * iOS adds a fourth, `.closed`, for an evicted session. The web client tracks
 * that separately (`channelStopped`) rather than folding it in here, because on
 * this platform it is a property of the socket's close policy, not of the
 * replay machine.
 */
export type TerminalConnectionState = 'connecting' | 'replaying' | 'live';

export interface ReplayState {
  connection: TerminalConnectionState;
  inReplay: boolean;
}

export type ReplayEffect =
  /** Feed {@link CLEAR_BEFORE_REPLAY} into the write stream. */
  | { kind: 'wipe' }
  | { kind: 'write'; payload: Uint8Array }
  /** Raw `s` frame JSON; the caller decodes and stores it. */
  | { kind: 'state'; json: string }
  /** Entered live: re-assert the cached grid size. */
  | { kind: 'enterLive' }
  /** The replay span closed; the caller reveals live once the writes drain. */
  | { kind: 'replayFinished' };

export interface ReplayTransition {
  state: ReplayState;
  effects: ReplayEffect[];
}

export function initialReplayState(): ReplayState {
  return { connection: 'connecting', inReplay: false };
}

/**
 * Fold one inbound frame into the machine.
 *
 * `frame` is nullable on purpose: an undecodable binary message still counts as
 * traffic and still upgrades a reconnecting channel, exactly as it does on
 * Android where the upgrade precedes the decode.
 */
export function reduceInboundFrame(
  state: ReplayState,
  frame: TerminalInboundFrame | null,
): ReplayTransition {
  const effects: ReplayEffect[] = [];
  let next = state;

  if (next.connection === 'connecting') {
    next = { ...next, connection: 'live' };
    effects.push({ kind: 'enterLive' });
  }

  switch (frame?.kind) {
    case 'replayStart':
      next = { connection: 'replaying', inReplay: true };
      effects.push({ kind: 'wipe' });
      break;
    case 'output':
      // The empty `'o'` the agent emits for an empty ring carries nothing to
      // write; both mobile clients skip it rather than round-trip the parser.
      if (frame.payload.length > 0) {
        effects.push({ kind: 'write', payload: frame.payload });
      }
      break;
    case 'state':
      effects.push({ kind: 'state', json: frame.json });
      break;
    case 'replayEnd':
      if (next.inReplay) {
        next = { connection: 'live', inReplay: false };
        effects.push({ kind: 'replayFinished' }, { kind: 'enterLive' });
      }
      break;
    default:
      break;
  }

  return { state: next, effects };
}

/**
 * The socket dropped: back to reconnecting, and out of any replay span.
 *
 * Dropping `inReplay` matters — a socket that dies mid-replay would otherwise
 * leave the next cycle's `'e'` matching a span that never finished, and the
 * overlay would clear a replay early.
 */
export function reduceDisconnected(): ReplayState {
  return { connection: 'connecting', inReplay: false };
}

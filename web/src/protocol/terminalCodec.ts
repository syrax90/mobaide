/**
 * Binary terminal frame grammar (PROTOCOL.md; Android `TerminalProtocol.kt`,
 * iOS `TerminalFrame.swift`).
 *
 * Every WebSocket binary message is exactly one frame: a single type byte at
 * `[0]` followed by the raw payload at `[1…]`.
 *
 * | Byte | Char | Direction | Payload                                          |
 * |------|------|-----------|--------------------------------------------------|
 * | 0x6f | 'o'  | S→C       | raw PTY output bytes (replay chunk or live)       |
 * | 0x69 | 'i'  | C→S       | raw input bytes (CR / Ctrl / Esc / UTF-8)         |
 * | 0x78 | 'x'  | C→S       | execute: agent sends Ctrl+C, payload, delayed CR  |
 * | 0x6d | 'm'  | C→S       | submit: agent writes payload, pauses, writes CR   |
 * | 0x72 | 'r'  | C→S       | resize: u16 rows + u16 cols, big-endian           |
 * | 0x63 | 'c'  | C→S       | clear: drop retained output (empty payload)       |
 * | 0x73 | 's'  | S→C       | state: UTF-8 JSON {status, pid?, exit_code?}      |
 * | 0x62 | 'b'  | S→C       | replay-buffer start marker (empty payload)        |
 * | 0x65 | 'e'  | S→C       | replay-buffer end marker (empty payload)          |
 *
 * Decoding is server→client only: an empty frame, an unknown type byte, and any
 * of the outbound type bytes all decode to `null`. An `'o'` frame with an empty
 * payload is *not* null — the server emits one even for an empty ring, so the
 * replay span always contains at least one output frame.
 *
 * Clients never append a trailing CR to `execute`/`submit` payloads; the agent
 * owns that timing (proto-v4).
 */
export const TERMINAL_FRAME_TYPE = {
  output: 0x6f,
  input: 0x69,
  execute: 0x78,
  submit: 0x6d,
  resize: 0x72,
  clear: 0x63,
  state: 0x73,
  replayStart: 0x62,
  replayEnd: 0x65,
} as const;

export type TerminalOutboundFrame =
  | { kind: 'input'; payload: Uint8Array }
  | { kind: 'execute'; payload: Uint8Array }
  | { kind: 'submit'; payload: Uint8Array }
  | { kind: 'resize'; rows: number; cols: number }
  | { kind: 'clear' };

export type TerminalInboundFrame =
  | { kind: 'output'; payload: Uint8Array }
  | { kind: 'state'; json: string }
  | { kind: 'replayStart' }
  | { kind: 'replayEnd' };

const encoder = new TextEncoder();
const decoder = new TextDecoder();

/** Wrap UTF-8 text in a text-carrying outbound frame. */
export function terminalTextFrame(
  kind: 'input' | 'execute' | 'submit',
  text: string,
): TerminalOutboundFrame {
  return { kind, payload: encoder.encode(text) };
}

function clampU16(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(0xffff, Math.max(0, Math.trunc(value)));
}

export function encodeTerminalFrame(frame: TerminalOutboundFrame): Uint8Array {
  if (frame.kind === 'resize') {
    const rows = clampU16(frame.rows);
    const cols = clampU16(frame.cols);
    const out = new Uint8Array(5);
    out[0] = TERMINAL_FRAME_TYPE.resize;
    const view = new DataView(out.buffer);
    view.setUint16(1, rows);
    view.setUint16(3, cols);
    return out;
  }
  if (frame.kind === 'clear') {
    return Uint8Array.of(TERMINAL_FRAME_TYPE.clear);
  }
  const type = TERMINAL_FRAME_TYPE[frame.kind];
  const out = new Uint8Array(frame.payload.length + 1);
  out[0] = type;
  out.set(frame.payload, 1);
  return out;
}

export function decodeTerminalFrame(frame: Uint8Array): TerminalInboundFrame | null {
  const type = frame[0];
  if (type === undefined) return null;
  switch (type) {
    case TERMINAL_FRAME_TYPE.output:
      return { kind: 'output', payload: frame.subarray(1) };
    case TERMINAL_FRAME_TYPE.state:
      return { kind: 'state', json: decoder.decode(frame.subarray(1)) };
    case TERMINAL_FRAME_TYPE.replayStart:
      return { kind: 'replayStart' };
    case TERMINAL_FRAME_TYPE.replayEnd:
      return { kind: 'replayEnd' };
    default:
      return null;
  }
}

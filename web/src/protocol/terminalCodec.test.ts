// @vitest-environment node
//
// Node, not jsdom: the assertions are on hex strings produced from typed
// arrays, and typed-array identity across the jsdom realm boundary is a known
// source of flakes.
import { describe, expect, it } from 'vitest';
import { bytesToHex, hexToBytes, readFixture } from './__fixtures__/load';
import {
  decodeTerminalFrame,
  encodeTerminalFrame,
  terminalTextFrame,
  type TerminalOutboundFrame,
} from './terminalCodec';

interface EncodeCase {
  name: string;
  frame: { kind: string; rows?: number; cols?: number; text?: string };
  bytes_hex: string;
}

interface DecodeCase {
  name: string;
  bytes_hex: string;
  expect: {
    kind: string;
    payload_hex?: string;
    status?: string;
    pid?: number;
  } | null;
}

interface TerminalFramesFixture {
  encode: EncodeCase[];
  decode: DecodeCase[];
}

const fixture = readFixture<TerminalFramesFixture>('frames/terminal_frames.json');

/** Fail loudly on an unhandled kind rather than skipping the case. */
function frameFromSpec(spec: EncodeCase['frame'], caseName: string): TerminalOutboundFrame {
  switch (spec.kind) {
    case 'resize':
      return { kind: 'resize', rows: spec.rows ?? 0, cols: spec.cols ?? 0 };
    case 'input':
    case 'execute':
    case 'submit':
      return terminalTextFrame(spec.kind, spec.text ?? '');
    default:
      throw new Error(`${caseName}: unknown frame kind ${spec.kind}`);
  }
}

describe('terminal codec golden fixtures', () => {
  it('has cases to run', () => {
    expect(fixture.encode.length).toBeGreaterThan(0);
    expect(fixture.decode.length).toBeGreaterThan(0);
  });

  it.each(fixture.encode)('encodes $name to the golden bytes', (encodeCase) => {
    const frame = frameFromSpec(encodeCase.frame, encodeCase.name);
    expect(bytesToHex(encodeTerminalFrame(frame))).toBe(encodeCase.bytes_hex);
  });

  it.each(fixture.decode)('decodes $name as expected', (decodeCase) => {
    const decoded = decodeTerminalFrame(hexToBytes(decodeCase.bytes_hex));
    const expected = decodeCase.expect;

    if (expected === null) {
      expect(decoded).toBeNull();
      return;
    }

    expect(decoded).not.toBeNull();
    switch (expected.kind) {
      case 'output': {
        expect(decoded?.kind).toBe('output');
        const payload = decoded?.kind === 'output' ? decoded.payload : new Uint8Array();
        expect(bytesToHex(payload)).toBe(expected.payload_hex ?? '');
        break;
      }
      case 'state': {
        expect(decoded?.kind).toBe('state');
        const json = decoded?.kind === 'state' ? decoded.json : '';
        // Strict equality on the whole tree, like the iOS assertion: a state
        // frame that grew an unexpected field must fail, not pass silently.
        const wanted: Record<string, unknown> = {};
        if (expected.status !== undefined) wanted.status = expected.status;
        if (expected.pid !== undefined) wanted.pid = expected.pid;
        expect(JSON.parse(json)).toEqual(wanted);
        break;
      }
      case 'replay_start':
        expect(decoded?.kind).toBe('replayStart');
        break;
      case 'replay_end':
        expect(decoded?.kind).toBe('replayEnd');
        break;
      default:
        throw new Error(`${decodeCase.name}: unhandled expectation ${expected.kind}`);
    }
  });
});

describe('terminal codec edges the fixtures do not cover', () => {
  it('keeps an empty output payload rather than dropping the frame', () => {
    // The agent emits one empty 'o' even for an empty ring, so the replay span
    // always contains at least one output frame.
    const decoded = decodeTerminalFrame(new Uint8Array([0x6f]));
    expect(decoded).toEqual({ kind: 'output', payload: new Uint8Array() });
  });

  it('clamps an out-of-range grid into the u16 the wire allows', () => {
    expect(bytesToHex(encodeTerminalFrame({ kind: 'resize', rows: -5, cols: 70000 }))).toBe(
      '720000ffff',
    );
  });

  it('carries high bytes through an output payload untouched', () => {
    const payload = encodeTerminalFrame(terminalTextFrame('input', 'h\u00e9llo\u001b[A')).subarray(1);
    const decoded = decodeTerminalFrame(new Uint8Array([0x6f, ...payload]));
    expect(decoded?.kind).toBe('output');
    expect(bytesToHex(decoded?.kind === 'output' ? decoded.payload : new Uint8Array())).toBe(
      bytesToHex(payload),
    );
  });
});

// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { readFixture } from './__fixtures__/load';
import {
  AutoScanCursor,
  buildSubscribeMessage,
  decodeAutoScanFrame,
} from './autoscanFrames';
import { stringifyWire } from './wireJson';

interface AutoScanCase {
  name: string;
  wire: unknown;
  expect: {
    kind: string;
    project_name?: string;
    seq?: number;
    payload_type?: string;
    scan_count?: number;
    event_count?: number;
  };
}

const fixture = readFixture<{ cases: AutoScanCase[] }>('frames/autoscan_frames.json');

describe('auto-scan frame golden fixtures', () => {
  it('has cases to run', () => {
    expect(fixture.cases.length).toBeGreaterThanOrEqual(10);
  });

  it.each(fixture.cases)('decodes $name', (testCase) => {
    // A fresh cursor per case: the fixture cases are independent samples, not
    // one ordered stream.
    const frame = decodeAutoScanFrame(stringifyWire(testCase.wire), new AutoScanCursor());
    const expected = testCase.expect;

    if (expected.kind === 'null') {
      expect(frame).toBeNull();
      return;
    }

    expect(frame).not.toBeNull();
    switch (expected.kind) {
      case 'caught_up':
        expect(frame?.kind).toBe('caughtUp');
        break;
      case 'event': {
        expect(frame?.kind).toBe('event');
        if (frame?.kind !== 'event') break;
        expect(frame.event.projectName).toBe(expected.project_name);
        expect(frame.event.seq).toBe(BigInt(expected.seq ?? 0));
        // The fixture calls an unrecognised inner type "unknown"; the wire
        // string is preserved verbatim here rather than coerced, so an unknown
        // type is anything outside the five the payload parser models.
        const known = ['phase', 'issue', 'worktree', 'session', 'log'];
        const observed = known.includes(frame.event.type) ? frame.event.type : 'unknown';
        expect(observed).toBe(expected.payload_type);
        break;
      }
      case 'snapshot': {
        expect(frame?.kind).toBe('snapshot');
        if (frame?.kind !== 'snapshot') break;
        expect(frame.scans).toHaveLength(expected.scan_count ?? 0);
        const eventCount = Object.values(frame.events).reduce(
          (total, list) => total + list.length,
          0,
        );
        expect(eventCount).toBe(expected.event_count ?? 0);
        break;
      }
      default:
        throw new Error(`${testCase.name}: unhandled expectation ${expected.kind}`);
    }
  });
});

describe('auto-scan wire cursor', () => {
  const event = (projectName: string, seq: number): string =>
    stringifyWire({
      type: 'event',
      project_name: projectName,
      event: { seq, ts: 't', type: 'log', payload: { line: 'x' } },
    });

  it('drops a replayed event and keeps the newer one', () => {
    const cursor = new AutoScanCursor();
    expect(decodeAutoScanFrame(event('a', 5), cursor)).not.toBeNull();
    expect(decodeAutoScanFrame(event('a', 5), cursor)).toBeNull();
    expect(decodeAutoScanFrame(event('a', 4), cursor)).toBeNull();
    expect(decodeAutoScanFrame(event('a', 6), cursor)).not.toBeNull();
  });

  it('advances on snapshot-buffered events, so the resume mark survives a reconnect', () => {
    const cursor = new AutoScanCursor();
    const snapshot = stringifyWire({
      type: 'snapshot',
      data: {
        scans: [],
        events: { 'proj-a': [{ seq: 3, ts: 't', type: 'log', payload: {} }] },
      },
    });
    expect(decodeAutoScanFrame(snapshot, cursor)).not.toBeNull();
    expect(buildSubscribeMessage([], cursor)).toBe(
      '{"subscribe":[],"resume_from_seq":{"proj-a":3}}',
    );
  });

  it('omits a project whose buffered events all deduped away', () => {
    const cursor = new AutoScanCursor({ 'proj-a': 5n });
    const snapshot = stringifyWire({
      type: 'snapshot',
      data: {
        scans: [],
        events: { 'proj-a': [{ seq: 3, ts: 't', type: 'log', payload: {} }] },
      },
    });
    const frame = decodeAutoScanFrame(snapshot, cursor);
    expect(frame?.kind).toBe('snapshot');
    expect(frame?.kind === 'snapshot' ? frame.events : {}).toEqual({});
  });
});

describe('subscribe message', () => {
  it('always sends both keys, even when empty', () => {
    expect(buildSubscribeMessage([], new AutoScanCursor())).toBe(
      '{"subscribe":[],"resume_from_seq":{}}',
    );
  });

  it('carries the subscription set and the live cursor', () => {
    const cursor = new AutoScanCursor({ 'proj-a': 42n });
    expect(buildSubscribeMessage(['proj-a'], cursor)).toBe(
      '{"subscribe":["proj-a"],"resume_from_seq":{"proj-a":42}}',
    );
  });
});

describe('snapshot events keyed by hostile project names', () => {
  it('keeps a project literally named __proto__ instead of re-parenting the map', () => {
    // JSON.parse yields `__proto__` as an *own* key, so a member assignment
    // would hit Object.prototype's setter: the events vanish while the cursor
    // has already advanced past them, so a resume can never recover them.
    const cursor = new AutoScanCursor();
    // Raw text, not an object literal: `__proto__:` in a literal *is* the
    // prototype-setter syntax, so it would never reach the decoder as a key.
    const snapshot =
      '{"type":"snapshot","data":{"scans":[],' +
      '"events":{"__proto__":[{"seq":1,"ts":"t","type":"log","payload":{}}]}}}';

    const frame = decodeAutoScanFrame(snapshot, cursor);

    expect(frame?.kind).toBe('snapshot');
    const events = frame?.kind === 'snapshot' ? frame.events : {};
    expect(Object.keys(events)).toEqual(['__proto__']);
    expect(Object.getPrototypeOf(events)).toBe(Object.prototype);
  });
});

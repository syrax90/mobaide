/**
 * Wire half of the `ws/autoscan` streaming protocol (agent
 * `mobaide/agent/autoscan/stream.py`, protocol v1).
 *
 * Sequence per connection: the client sends
 * `{subscribe, resume_from_seq}` inside the server's grace window (200 ms
 * direct, 1 s through the relay), then receives `snapshot` → `caught_up` →
 * live `event` frames.
 *
 * The cursor here is the *wire* cursor, and it is what makes resume correct
 * across reconnects: it is seeded from `resume_from_seq`, advanced by every
 * inbound event — snapshot-buffered and live alike — and the subscribe frame is
 * rebuilt from it on every reopen. Android instead re-sends the map captured at
 * connect time, so a reconnect there resumes from a stale mark; iOS fixed that
 * and this is the iOS behaviour.
 *
 * Server-side resume is best-effort: if its ring has already rolled past the
 * cursor it replays the whole ring, so dropping `seq <= cursor` on the client is
 * required, not an optimisation.
 *
 * Reducing frames into UI state (and the second, per-view reduction cursor) is
 * WEB-10's job — this module stops at the wire.
 */
import { parseWireJson, stringifyWire, toWireSeq } from './wireJson';

export interface AutoScanEvent {
  projectName: string;
  seq: bigint;
  ts: string;
  /** Open string: `phase` | `issue` | `worktree` | `session` | `log` | anything newer. */
  type: string;
  payload: Record<string, unknown>;
}

export type AutoScanFrame =
  | {
      kind: 'snapshot';
      /** Left unmodelled on purpose — `AutoScanInstanceDto` lands with WEB-10. */
      scans: unknown[];
      events: Record<string, AutoScanEvent[]>;
    }
  | { kind: 'caughtUp' }
  | { kind: 'event'; event: AutoScanEvent };

/** Per-project high-water mark of the sequence numbers already delivered. */
export class AutoScanCursor {
  private readonly marks = new Map<string, bigint>();

  constructor(initial: Readonly<Record<string, bigint>> = {}) {
    for (const [project, seq] of Object.entries(initial)) {
      this.marks.set(project, seq);
    }
  }

  /** Accept and record `seq`, or reject it as already-delivered. */
  admit(projectName: string, seq: bigint): boolean {
    const last = this.marks.get(projectName) ?? 0n;
    if (seq <= last) return false;
    this.marks.set(projectName, seq);
    return true;
  }

  resumeFromSeq(): Record<string, bigint> {
    return Object.fromEntries(this.marks);
  }
}

/**
 * The client's opening config frame.
 *
 * Both keys are always emitted, even when empty — the shape Android's golden
 * test asserts and the server's `payload.get(...) or {}` tolerates either way.
 * `resume_from_seq` values go out as bare JSON integers because the server does
 * `int(v)` on them.
 */
export function buildSubscribeMessage(
  subscribe: readonly string[],
  cursor: AutoScanCursor,
): string {
  return stringifyWire({
    subscribe: [...subscribe],
    resume_from_seq: cursor.resumeFromSeq(),
  });
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function asString(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function parseEvent(
  projectName: string,
  raw: Record<string, unknown>,
  cursor: AutoScanCursor,
): AutoScanEvent | null {
  const seq = toWireSeq(raw.seq);
  if (!cursor.admit(projectName, seq)) return null;
  return {
    projectName,
    seq,
    ts: asString(raw.ts),
    type: asString(raw.type),
    payload: asRecord(raw.payload) ?? {},
  };
}

/**
 * Decode one inbound text frame, advancing `cursor`.
 *
 * Returns `null` for anything the frame contract does not cover — a malformed
 * body, a `snapshot` with no `data`, an `event` with no inner `event`, an
 * unknown `type`, or an event already past the cursor.
 */
export function decodeAutoScanFrame(
  text: string,
  cursor: AutoScanCursor,
): AutoScanFrame | null {
  let parsed: unknown;
  try {
    parsed = parseWireJson(text);
  } catch {
    return null;
  }
  const frame = asRecord(parsed);
  if (frame === null) return null;

  switch (frame.type) {
    case 'caught_up':
      return { kind: 'caughtUp' };

    case 'snapshot': {
      const data = asRecord(frame.data);
      if (data === null) return null;
      const scans = Array.isArray(data.scans) ? (data.scans as unknown[]) : [];
      const rawEvents = asRecord(data.events) ?? {};
      const entries: [string, AutoScanEvent[]][] = [];
      for (const [projectName, list] of Object.entries(rawEvents)) {
        if (!Array.isArray(list)) continue;
        const parsedEvents: AutoScanEvent[] = [];
        for (const item of list as unknown[]) {
          const record = asRecord(item);
          if (record === null) continue;
          const event = parseEvent(projectName, record, cursor);
          if (event !== null) parsedEvents.push(event);
        }
        // A project whose buffered events all deduped away is omitted rather
        // than reported as empty, so consumers cannot mistake replay for a
        // genuine "no activity" snapshot.
        if (parsedEvents.length > 0) entries.push([projectName, parsedEvents]);
      }
      // `Object.fromEntries`, not `events[projectName] = …`: project names come
      // off the wire, and `JSON.parse` yields `__proto__` as an *own* key, so a
      // member assignment would invoke `Object.prototype`'s `__proto__` setter
      // — re-parenting the accumulator and dropping that project's events after
      // the cursor had already advanced past them, which a resume can never
      // recover. `fromEntries` uses CreateDataProperty and cannot reach a setter.
      return { kind: 'snapshot', scans, events: Object.fromEntries(entries) };
    }

    case 'event': {
      const raw = asRecord(frame.event);
      if (raw === null) return null;
      const event = parseEvent(asString(frame.project_name), raw, cursor);
      if (event === null) return null;
      return { kind: 'event', event };
    }

    default:
      return null;
  }
}

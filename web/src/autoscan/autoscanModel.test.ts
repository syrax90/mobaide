import { describe, expect, it } from 'vitest';
import type { AutoScanEvent } from '../protocol/autoscanFrames';
import {
  initialAutoScanDetailState,
  LOG_RING_MAX,
  orderedPhases,
  PHASE_ORDER,
  reduceAutoScanFrame,
  reduceAutoScanFrames,
  resumeFromState,
  type AutoScanDetailState,
  type DetailFrame,
} from './autoscanModel';

const PROJECT = 'proj-a';

function event(
  seq: bigint,
  type: string,
  payload: Record<string, unknown>,
  projectName = PROJECT,
): AutoScanEvent {
  return { projectName, seq, ts: '2026-07-08T12:00:00Z', type, payload };
}

function frame(scanEvent: AutoScanEvent): DetailFrame {
  return { kind: 'event', event: scanEvent };
}

function texts(state: AutoScanDetailState): string[] {
  return state.logLines.map((line) => line.text);
}

function drive(frames: readonly DetailFrame[], from = initialAutoScanDetailState(PROJECT)) {
  return frames.reduce(reduceAutoScanFrame, from);
}

const BUSY_CYCLE: DetailFrame[] = [
  frame(event(1n, 'phase', { phase: 'scan_started', cycle_number: 4 })),
  frame(
    event(2n, 'issue', {
      identifier: 'ISS-1',
      title: 'Fix bug',
      step: 'state_update',
      status: 'started',
    }),
  ),
  frame(
    event(3n, 'issue', {
      identifier: 'ISS-1',
      title: 'Fix bug',
      step: 'state_update',
      status: 'ok',
    }),
  ),
  frame(event(4n, 'worktree', { identifier: 'ISS-1', path: '/w/ISS-1' })),
  frame(event(5n, 'session', { identifier: 'ISS-1', session_id: 's-1', pid: 4242 })),
  frame(
    event(6n, 'phase', {
      phase: 'scan_completed',
      cycle_number: 4,
      issues_processed: 1,
      next_scan_at: '2026-07-08T12:05:00Z',
    }),
  ),
];

describe('reduction cursor', () => {
  it('applies each event exactly once when the whole stream is replayed', () => {
    // The acceptance criterion: reconnect-and-resume must not duplicate a line.
    // The wire cursor covers one socket's replay; this covers reducing the same
    // frames twice, which is what a snapshot overlapping live events produces.
    const once = drive(BUSY_CYCLE);
    const twice = drive(BUSY_CYCLE, once);

    expect(twice.logLines).toEqual(once.logLines);
    expect(twice.recentIssues).toEqual(once.recentIssues);
    expect(twice).toBe(once);
  });

  it('drops an out-of-order lower seq', () => {
    const state = drive([
      frame(event(5n, 'log', { line: 'five' })),
      frame(event(3n, 'log', { line: 'three' })),
      frame(event(6n, 'log', { line: 'six' })),
    ]);
    expect(texts(state)).toEqual(['five', 'six']);
  });

  it('ignores events for another project on the shared channel', () => {
    const state = drive([frame(event(1n, 'log', { line: 'elsewhere' }, 'proj-b'))]);
    expect(state.logLines).toHaveLength(0);
    expect(state.lastSeq).toBe(0n);
  });

  it('keeps discriminating past 2 ** 53', () => {
    // A `number` cursor would compare these two as equal and drop the newer
    // event, stranding the resume mark for the rest of the connection.
    const high = 9007199254740993n;
    const state = drive([
      frame(event(high, 'log', { line: 'first' })),
      frame(event(high - 1n, 'log', { line: 'replayed' })),
      frame(event(high + 1n, 'log', { line: 'second' })),
    ]);
    expect(texts(state)).toEqual(['first', 'second']);
    expect(state.lastSeq).toBe(high + 1n);
    expect(resumeFromState(state)).toEqual({ [PROJECT]: high + 1n });
  });

  it('reports no resume mark before the first event', () => {
    expect(resumeFromState(initialAutoScanDetailState(PROJECT))).toEqual({});
  });
});

describe('snapshot', () => {
  const snapshot = (
    scans: unknown[],
    events: Record<string, AutoScanEvent[]> = {},
  ): DetailFrame => ({ kind: 'snapshot', scans, events });

  it('adopts the matching instance and replays its buffered events', () => {
    const state = drive([
      snapshot(
        [
          { project_name: 'other', status: 'idle', started_at: 1, last_activity_at: 1 },
          {
            project_name: PROJECT,
            status: 'scanning',
            started_at: 1751800000.5,
            last_activity_at: 1751800005.5,
            cycle_number: 5,
            next_scan_at: '2026-07-08T12:05:00Z',
          },
        ],
        { [PROJECT]: [event(1n, 'log', { line: 'buffered' })] },
      ),
    ]);

    expect(state.instance?.status).toBe('scanning');
    expect(state.cycleNumber).toBe(5);
    expect(state.nextScanAt).toBe('2026-07-08T12:05:00Z');
    expect(texts(state)).toEqual(['buffered']);
    expect(state.connection).toBe('replaying');
  });

  it('converges when the agent replays its whole ring past the cursor', () => {
    // The hole-in-ring case: the server skips its trim filter and resends
    // everything it still holds, so events the client already has come back.
    const live = drive([
      frame(event(10n, 'log', { line: 'ten' })),
      frame(event(11n, 'log', { line: 'eleven' })),
    ]);
    const resynced = reduceAutoScanFrame(
      live,
      snapshot([], {
        [PROJECT]: [
          event(9n, 'log', { line: 'nine' }),
          event(10n, 'log', { line: 'ten' }),
          event(11n, 'log', { line: 'eleven' }),
          event(12n, 'log', { line: 'twelve' }),
        ],
      }),
    );

    expect(texts(resynced)).toEqual(['ten', 'eleven', 'twelve']);
    expect(resynced.lastSeq).toBe(12n);
  });

  it('skips a scan row that fails its schema', () => {
    const state = drive([snapshot([{ project_name: PROJECT }, null, 7])]);
    expect(state.instance).toBeUndefined();
  });

  it('clears the instance once a snapshot stops carrying the project', () => {
    // A stopped scan drops out of `scans`. Holding the last row would leave the
    // screen rendering "Scanning" and a frozen count for the rest of its life,
    // with the connection-state fallback permanently shadowed.
    const running = drive([
      snapshot([
        {
          project_name: PROJECT,
          status: 'scanning',
          started_at: 1,
          last_activity_at: 1,
          cycle_number: 5,
          next_scan_at: '2026-07-08T12:05:00Z',
        },
      ]),
    ]);
    expect(running.instance?.status).toBe('scanning');

    const gone = reduceAutoScanFrame(running, snapshot([]));

    expect(gone.instance).toBeUndefined();
    expect(gone.nextScanAt).toBeUndefined();
    // The cycle survives: phase events drive it too, so a missing row is not a
    // reason to forget which cycle the log is showing.
    expect(gone.cycleNumber).toBe(5);
  });

  it('does not read buffered events off the prototype chain', () => {
    // `projectName` is a URL path segment. `events['__proto__']` and friends
    // resolve to a *non-nullish* inherited value, so `?? []` never fires and the
    // `for…of` would throw out of a React render — taking the whole app down via
    // the router's root error boundary.
    for (const hostile of ['__proto__', 'toString', 'constructor', 'valueOf']) {
      const state = initialAutoScanDetailState(hostile);
      expect(() =>
        reduceAutoScanFrame(state, { kind: 'snapshot', scans: [], events: {} }),
      ).not.toThrow();
      expect(reduceAutoScanFrame(state, { kind: 'snapshot', scans: [], events: {} }).logLines)
        .toHaveLength(0);
    }
  });

  it('still replays buffered events for a project literally named __proto__', () => {
    // Hardening must not silently drop a project the agent really reported —
    // `autoscanFrames.ts` goes out of its way to keep that key.
    const state = initialAutoScanDetailState('__proto__');
    const replayed = reduceAutoScanFrame(state, {
      kind: 'snapshot',
      scans: [],
      events: Object.fromEntries([
        ['__proto__', [event(1n, 'log', { line: 'hostile' }, '__proto__')]],
      ]),
    });

    expect(texts(replayed)).toEqual(['hostile']);
  });
});

describe('phase timeline', () => {
  it('renders in the fixed order regardless of arrival order', () => {
    const state = drive([
      frame(
        event(1n, 'issue', {
          identifier: 'ISS-1',
          title: 't',
          step: 'session_spawn',
          status: 'ok',
        }),
      ),
      frame(
        event(2n, 'issue', {
          identifier: 'ISS-1',
          title: 't',
          step: 'state_update',
          status: 'failed',
        }),
      ),
    ]);

    expect(orderedPhases(state).map((phase) => phase.key)).toEqual([...PHASE_ORDER]);
    expect(state.phases.spawn).toBe('ok');
    expect(state.phases.state).toBe('failed');
  });

  it('resets every phase on a new cycle but keeps recent issues', () => {
    const afterBusy = drive(BUSY_CYCLE);
    expect(afterBusy.recentIssues).toHaveLength(2);

    const restarted = reduceAutoScanFrame(
      afterBusy,
      frame(event(7n, 'phase', { phase: 'scan_started', cycle_number: 5 })),
    );

    expect(restarted.phases).toEqual({
      scan: 'active',
      state: 'pending',
      label: 'pending',
      worktree: 'pending',
      spawn: 'pending',
    });
    expect(restarted.recentIssues).toHaveLength(2);
    expect(restarted.cycleNumber).toBe(5);
  });

  it('marks untouched phases skipped and logs twice on an idle cycle', () => {
    const state = drive([
      frame(event(1n, 'phase', { phase: 'scan_started', cycle_number: 9 })),
      frame(event(2n, 'phase', { phase: 'scan_completed', cycle_number: 9, issues_processed: 0 })),
    ]);

    expect(state.phases).toEqual({
      scan: 'ok',
      state: 'skipped',
      label: 'skipped',
      worktree: 'skipped',
      spawn: 'skipped',
    });
    // One event, two log lines — which is exactly why the line key cannot be `seq`.
    expect(texts(state).slice(1)).toEqual([
      '--- Scan #9 completed ---',
      'no matching issues — skipping remaining stages',
    ]);
    expect(new Set(state.logLines.map((line) => line.id)).size).toBe(state.logLines.length);
    expect(state.activePhase).toBeUndefined();
  });

  it('leaves pending phases alone when the cycle did work', () => {
    const state = drive(BUSY_CYCLE);
    expect(state.phases.state).toBe('ok');
    expect(state.phases.label).toBe('pending');
    expect(state.nextScanAt).toBe('2026-07-08T12:05:00Z');
  });

  it('does not key the phase map by a wire-supplied step name', () => {
    // A member assignment keyed by `step` would reach Object.prototype's
    // `__proto__` setter and re-parent the map.
    const state = drive([
      frame(
        event(1n, 'issue', {
          identifier: 'ISS-1',
          title: 't',
          step: '__proto__',
          status: 'started',
        }),
      ),
    ]);

    expect(Object.getPrototypeOf(state.phases)).toBe(Object.prototype);
    expect(Object.keys(state.phases).sort()).toEqual([...PHASE_ORDER].sort());
    expect(state.recentIssues).toHaveLength(1);
  });
});

describe('log lines', () => {
  it('formats worktree, session and issue events like the mobile clients', () => {
    const state = drive([
      frame(event(1n, 'worktree', { identifier: 'ISS-1', path: '/w/ISS-1' })),
      frame(event(2n, 'session', { identifier: 'ISS-1', session_id: 's-1', pid: 4242 })),
      frame(event(3n, 'session', { identifier: 'ISS-2', session_id: 's-2' })),
      frame(
        event(4n, 'issue', {
          identifier: 'ISS-3',
          title: 't',
          step: 'label_removal',
          status: 'ok',
          error: "label 'AutoPipeline' not present on issue",
        }),
      ),
    ]);

    expect(texts(state)).toEqual([
      'worktree: /w/ISS-1',
      'session: s-1 (pid=4242)',
      'session: s-2',
      "ISS-3 label_removal ok [label 'AutoPipeline' not present on issue]",
    ]);
  });

  it('carries the ansi flag through for the renderer', () => {
    const state = drive([
      frame(event(1n, 'log', { line: 'plain' })),
      frame(event(2n, 'log', { line: 'coloured', ansi: true })),
    ]);
    expect(state.logLines.map((line) => line.ansi)).toEqual([false, true]);
  });

  it('trims the oldest line past the ring cap and never recycles an id', () => {
    const overflow = LOG_RING_MAX + 5;
    const frames = Array.from({ length: overflow }, (_, index) =>
      frame(event(BigInt(index + 1), 'log', { line: `line-${String(index)}` })),
    );
    const state = reduceAutoScanFrames(initialAutoScanDetailState(PROJECT), frames);

    expect(state.logLines).toHaveLength(LOG_RING_MAX);
    expect(state.logLines[0]?.text).toBe('line-5');
    expect(state.logLines[0]?.id).toBe(5);
    expect(state.nextLogId).toBe(overflow);
  });

  it('ignores an event whose payload fails its schema', () => {
    const state = drive([
      frame(event(1n, 'log', { level: 'info' })),
      frame(event(2n, 'mystery', {})),
    ]);
    expect(state.logLines).toHaveLength(0);
    // The cursor still advances: the agent will not resend these, so holding the
    // mark back would make every reconnect replay them again.
    expect(state.lastSeq).toBe(2n);
  });
});

describe('connection state', () => {
  it('walks connecting → replaying → connected and back on a drop', () => {
    let state = initialAutoScanDetailState(PROJECT);
    expect(state.connection).toBe('connecting');

    state = reduceAutoScanFrame(state, { kind: 'connected' });
    expect(state.connection).toBe('connecting');

    state = reduceAutoScanFrame(state, { kind: 'snapshot', scans: [], events: {} });
    expect(state.connection).toBe('replaying');

    state = reduceAutoScanFrame(state, { kind: 'caughtUp' });
    expect(state.connection).toBe('connected');

    state = reduceAutoScanFrame(state, { kind: 'disconnected' });
    expect(state.connection).toBe('disconnected');
  });

  it('returns the same state object when a batch changes nothing', () => {
    const state = drive(BUSY_CYCLE);
    expect(reduceAutoScanFrames(state, [])).toBe(state);
    expect(reduceAutoScanFrames(state, [frame(event(1n, 'log', { line: 'old' }))])).toBe(state);
  });
});

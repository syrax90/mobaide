import { describe, expect, it } from 'vitest';
import {
  autoScanStatusLabel,
  autoScanTone,
  countdownLabel,
  issueTone,
  parseNextScanAt,
  phaseLabel,
  phaseTone,
  relativeTime,
  remainingScanSlots,
  sortScans,
} from './autoscanDisplay';
import { autoScanInstanceSchema, type AutoScanInstance } from '../protocol/dto/autoscan';

function scan(overrides: Record<string, unknown>): AutoScanInstance {
  // Built through the schema so the optional-but-required-key shape its
  // transforms produce is the one under test, not a hand-written approximation.
  return autoScanInstanceSchema.parse({
    project_name: 'p',
    status: 'scanning',
    started_at: 1,
    last_activity_at: 1,
    ...overrides,
  });
}

describe('status mapping', () => {
  it.each([
    ['scanning', 'running'],
    ['error', 'crashed'],
  ])('%s is toned %s', (status, tone) => {
    expect(autoScanTone(status)).toBe(tone);
  });

  it('tones an unrecognised status as inert rather than running', () => {
    // `status` is an open wire string; a newer agent value must not read as live.
    expect(autoScanTone('quiescing')).toBe('stopped');
    expect(autoScanStatusLabel('quiescing')).toBe('Quiescing');
  });

  it('maps phase and issue statuses', () => {
    expect(phaseTone('active')).toBe('pending');
    expect(phaseTone('ok')).toBe('running');
    expect(phaseTone('failed')).toBe('crashed');
    expect(phaseTone('skipped')).toBe('stopped');
    expect(issueTone('started')).toBe('pending');
    expect(issueTone('whatever')).toBe('stopped');
    expect(phaseLabel('worktree')).toBe('Worktree');
    expect(phaseLabel('constructor')).toBe('constructor');
  });
});

describe('relativeTime', () => {
  const now = 1_751_800_000_000;

  it.each([
    [now / 1000 - 1, 'just now'],
    [now / 1000 - 120, '2m ago'],
    [now / 1000 - 172_800, '2d ago'],
  ])('renders %s as %s', (epochSeconds, expected) => {
    expect(relativeTime(epochSeconds, now)).toBe(expected);
  });

  it('renders nothing for a missing timestamp', () => {
    expect(relativeTime(0, now)).toBe('');
  });
});

describe('countdownLabel', () => {
  const now = Date.parse('2026-07-08T12:00:00Z');

  it('counts down in minutes and seconds', () => {
    expect(countdownLabel('2026-07-08T12:02:05Z', now)).toBe('Next scan in 2m 5s');
    expect(countdownLabel('2026-07-08T12:00:09Z', now)).toBe('Next scan in 9s');
  });

  it('says now once the target has passed', () => {
    expect(countdownLabel('2026-07-08T11:59:00Z', now)).toBe('Next scan in now');
  });

  it('accepts the offset spelling the agent actually emits', () => {
    // `datetime.isoformat()` gives `+00:00` with fractional seconds; the shared
    // fixtures use `Z`. Both have to work.
    expect(parseNextScanAt('2026-07-08T12:05:00.123456+00:00')).toBe(
      Date.parse('2026-07-08T12:05:00.123Z'),
    );
  });

  it('rejects a naive timestamp instead of reading it as local time', () => {
    // A missing offset would silently shift the countdown by the viewer's UTC
    // offset — a wrong countdown is worse than none.
    expect(parseNextScanAt('2026-07-08T12:05:00')).toBeNull();
    expect(countdownLabel('2026-07-08T12:05:00', now)).toBeNull();
  });

  it('has nothing to show without a scheduled scan', () => {
    expect(countdownLabel(undefined, now)).toBeNull();
    expect(countdownLabel('', now)).toBeNull();
    expect(countdownLabel('not a date', now)).toBeNull();
  });
});

describe('capacity', () => {
  it('reports headroom, not the running count the field name suggests', () => {
    expect(remainingScanSlots({ capacity: 4, max_scans: 5 })).toBe(1);
    expect(remainingScanSlots({ capacity: 5, max_scans: 5 })).toBe(0);
    expect(remainingScanSlots({ capacity: 9, max_scans: 5 })).toBe(0);
  });
});

describe('sortScans', () => {
  it('puts the most recently active first and breaks ties by name', () => {
    const sorted = sortScans([
      scan({ project_name: 'b', last_activity_at: 10 }),
      scan({ project_name: 'c', last_activity_at: 30 }),
      scan({ project_name: 'a', last_activity_at: 10 }),
    ]);
    expect(sorted.map((entry) => entry.project_name)).toEqual(['c', 'a', 'b']);
  });
});

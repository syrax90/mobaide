import { describe, expect, it } from 'vitest';
import { sessionResponseSchema, type SessionResponse } from '../../protocol/dto/session';
import {
  attentionChip,
  canStopSession,
  customTypeNameMap,
  isSessionWorking,
  nearestOpenSession,
  sessionDialogLabel,
  sessionHeaderLabel,
  sessionKindLabel,
  sessionStateTone,
  sessionStatusChip,
  sessionTitle,
} from './sessionDisplay';

/** Built through the schema so the defaults these helpers read are the wire's. */
const session = (over: Record<string, unknown> = {}): SessionResponse =>
  sessionResponseSchema.parse({
    session_id: '0123456789abcdef',
    state: 'running',
    created_at: '2026-08-04T10:00:00Z',
    ...over,
  });

const names = customTypeNameMap([{ id: '0123456789ab', name: 'My Linter' }]);

describe('sessionKindLabel', () => {
  it.each([
    ['claude', 'Claude Code'],
    ['codex', 'Codex'],
    ['antigravity', 'Antigravity'],
    ['auto_scan', 'Auto Scan'],
    ['shell', 'Terminal'],
  ])('names the built-in kind %s', (kind, expected) => {
    expect(sessionKindLabel(session({ kind }), names)).toBe(expected);
  });

  it('prefers the snapshotted label, then the live registry, then "Custom"', () => {
    const kind = 'custom_0123456789ab';

    expect(sessionKindLabel(session({ kind, kind_label: 'Renamed' }), names)).toBe('Renamed');
    expect(sessionKindLabel(session({ kind }), names)).toBe('My Linter');
    expect(sessionKindLabel(session({ kind: 'custom_ffffffffffff' }), names)).toBe('Custom');
  });

  it('never renders a raw token for a kind this client has not heard of', () => {
    // A future built-in reaching an older client must still read as something.
    expect(sessionKindLabel(session({ kind: 'gemini' }), names)).toBe('Custom');
    expect(sessionKindLabel(session({ kind: 'gemini', kind_label: 'Gemini' }), names)).toBe(
      'Gemini',
    );
  });

  it('ignores a kind that only matches an inherited object key', () => {
    expect(sessionKindLabel(session({ kind: 'constructor' }), names)).toBe('Custom');
  });
});

describe('sessionTitle', () => {
  it('uses the agent-supplied title when there is one', () => {
    expect(sessionTitle(session({ title: 'Fix the parser' }), 'Claude Code')).toBe(
      'Fix the parser',
    );
  });

  it('falls back to the kind and the project, then to a short id', () => {
    expect(sessionTitle(session({ project_name: 'mobaide' }), 'Claude Code')).toBe(
      'Claude Code · mobaide',
    );
    expect(sessionTitle(session(), 'Terminal')).toBe('Terminal · 01234567');
    // An empty title is the agent saying nothing, not a title of "".
    expect(sessionTitle(session({ title: '', project_name: 'mobaide' }), 'Codex')).toBe(
      'Codex · mobaide',
    );
    // So is a whitespace-only one, as on both mobile clients.
    expect(sessionTitle(session({ title: '   ', project_name: 'mobaide' }), 'Codex')).toBe(
      'Codex · mobaide',
    );
  });
});

describe('sessionDialogLabel', () => {
  it('uses the agent-supplied title when there is one', () => {
    expect(sessionDialogLabel(session({ title: 'Fix the parser', kind: 'codex' }), names)).toBe(
      'Fix the parser',
    );
  });

  it('falls back to the kind and the project, so two of a kind stay apart', () => {
    expect(sessionDialogLabel(session({ kind: 'codex', project_name: 'mobaide' }), names)).toBe(
      'Codex · mobaide',
    );
    expect(
      sessionDialogLabel(session({ kind: 'custom_0123456789ab', project_name: 'mobaide' }), names),
    ).toBe('My Linter · mobaide');
  });

  it('falls back to the bare kind when there is no project either', () => {
    expect(sessionDialogLabel(session({ kind: 'shell' }), names)).toBe('Terminal');
  });

  it('treats a blank title or project as absent', () => {
    expect(sessionDialogLabel(session({ title: '   ', project_name: 'mobaide' }), names)).toBe(
      'Claude Code · mobaide',
    );
    expect(sessionDialogLabel(session({ title: '', project_name: '  ' }), names)).toBe(
      'Claude Code',
    );
  });

  it('never renders the session id, in any branch', () => {
    // The whole point of the helper: a prompt asking you to destroy something
    // must name it in words, and `sessionTitle`'s composite can end in an id.
    for (const over of [{}, { project_name: 'mobaide' }, { title: '' }, { kind: 'shell' }]) {
      expect(sessionDialogLabel(session(over), names)).not.toContain('0123');
    }
  });
});

describe('sessionHeaderLabel', () => {
  it('uses the agent-supplied title when there is one', () => {
    expect(sessionHeaderLabel(session({ title: 'Fix the parser', kind: 'codex' }), names)).toBe(
      'Fix the parser',
    );
  });

  it('falls back to the kind, never to an id', () => {
    // The header names one session; a truncated id would identify nothing.
    expect(sessionHeaderLabel(session({ kind: 'codex' }), names)).toBe('Codex');
    expect(sessionHeaderLabel(session({ title: '', project_name: 'mobaide' }), names)).toBe(
      'Claude Code',
    );
    expect(sessionHeaderLabel(session({ title: '   ', kind: 'shell' }), names)).toBe('Terminal');
  });

  it('resolves a custom kind through the live registry', () => {
    expect(sessionHeaderLabel(session({ kind: 'custom_0123456789ab' }), names)).toBe('My Linter');
  });
});

describe('sessionStateTone', () => {
  it.each([
    ['running', 'running'],
    ['starting', 'pending'],
    ['stopping', 'pending'],
    ['crashed', 'crashed'],
    ['stopped', 'stopped'],
    ['whatever-comes-next', 'stopped'],
  ])('tones %s as %s', (state, expected) => {
    expect(sessionStateTone(state)).toBe(expected);
  });
});

const CHIPS: [string, { text: string; tone: string }][] = [
  ['awaiting_permission', { text: 'Needs permission', tone: 'permission' }],
  ['awaiting_os_permission', { text: 'Needs permission', tone: 'permission' }],
  ['turn_finished', { text: 'Ready', tone: 'ready' }],
  ['ended', { text: 'Ended', tone: 'ended' }],
];

describe('attentionChip', () => {
  it.each(CHIPS)('renders %s', (attention, expected) => {
    expect(attentionChip(attention)).toEqual(expected);
  });

  it('renders nothing for an absent or unrecognised reason', () => {
    expect(attentionChip(undefined)).toBeNull();
    // A newer agent inventing a reason should be invisible, not garbled.
    expect(attentionChip('awaiting_biometric')).toBeNull();
  });
});

describe('isSessionWorking', () => {
  it('is working only for a running session the agent reports mid-turn', () => {
    expect(isSessionWorking(session({ activity: 'working' }))).toBe(true);
    expect(isSessionWorking(session({ activity: 'idle' }))).toBe(false);
    // Codex, Antigravity and Terminal sessions, or an older agent.
    expect(isSessionWorking(session())).toBe(false);
  });

  it.each(['starting', 'stopping', 'stopped', 'crashed'])(
    'never spins a %s session, whatever its last activity',
    (state) => {
      expect(isSessionWorking(session({ state, activity: 'working' }))).toBe(false);
    },
  );

  it.each(['awaiting_permission', 'awaiting_os_permission'])(
    'yields to %s: the turn is blocked on the user',
    (attention) => {
      expect(isSessionWorking(session({ activity: 'working', attention }))).toBe(false);
    },
  );
});

describe('sessionStatusChip', () => {
  it('shows Working over a standing Ready from an earlier turn', () => {
    expect(
      sessionStatusChip(session({ activity: 'working', attention: 'turn_finished' })),
    ).toEqual({ text: 'Working', tone: 'working' });
  });

  it('falls back to the attention chip once the turn is not working', () => {
    expect(
      sessionStatusChip(session({ activity: 'idle', attention: 'turn_finished' })),
    ).toEqual({ text: 'Ready', tone: 'ready' });
    expect(
      sessionStatusChip(session({ activity: 'working', attention: 'awaiting_permission' })),
    ).toEqual({ text: 'Needs permission', tone: 'permission' });
    expect(sessionStatusChip(session({ activity: 'idle' }))).toBeNull();
  });
});

describe('canStopSession', () => {
  it('offers Stop only while there is something to stop', () => {
    expect(canStopSession('running')).toBe(true);
    expect(canStopSession('starting')).toBe(true);
    expect(canStopSession('stopping')).toBe(false);
    expect(canStopSession('stopped')).toBe(false);
    expect(canStopSession('crashed')).toBe(false);
  });
});

describe('nearestOpenSession', () => {
  const row = (id: string, state = 'running') => session({ session_id: id, state });

  it('prefers the row below, then the row above', () => {
    const list = [row('a'), row('b'), row('c')];

    expect(nearestOpenSession(list, list, 'b')).toBe('c');
    expect(nearestOpenSession(list, list, 'c')).toBe('b');
    expect(nearestOpenSession(list, list, 'a')).toBe('b');
  });

  it('widens outward past rows that are not open', () => {
    const list = [row('a'), row('b', 'stopped'), row('c'), row('d', 'crashed'), row('e')];

    // One away on both sides is closed; two below beats two above.
    expect(nearestOpenSession(list, list, 'c')).toBe('e');
    expect(nearestOpenSession(list, [row('a'), row('c'), row('d', 'crashed')], 'c')).toBe('a');
  });

  it('counts a starting session as open', () => {
    const list = [row('a', 'stopping'), row('b'), row('c', 'starting')];

    expect(nearestOpenSession(list, list, 'b')).toBe('c');
  });

  it('keeps the snapshot positions after the stopped row has left the current list', () => {
    // The agent's push dropped the stopped row before the stop resolved.
    const snapshot = [row('a'), row('b'), row('c'), row('d')];
    const current = [row('a'), row('c'), row('d')];

    expect(nearestOpenSession(snapshot, current, 'b')).toBe('c');
  });

  it('skips a neighbour that has since gone or stopped', () => {
    const snapshot = [row('a'), row('b'), row('c'), row('d')];

    expect(nearestOpenSession(snapshot, [row('a'), row('b'), row('d')], 'b')).toBe('a');
    expect(nearestOpenSession(snapshot, [row('a', 'stopped'), row('c', 'stopping'), row('d')], 'b')).toBe(
      'd',
    );
  });

  it('ignores sessions that only appeared after the snapshot', () => {
    const snapshot = [row('a', 'stopped'), row('b')];

    expect(nearestOpenSession(snapshot, [row('a', 'stopped'), row('z')], 'b')).toBeNull();
  });

  it('returns null when no open session is left', () => {
    expect(nearestOpenSession([row('a')], [], 'a')).toBeNull();
    expect(nearestOpenSession([row('a', 'stopped'), row('b')], [row('a', 'stopped')], 'b')).toBeNull();
  });

  it('returns null when the stopped session was not in the snapshot', () => {
    expect(nearestOpenSession([row('a'), row('b')], [row('a'), row('b')], 'x')).toBeNull();
  });
});

describe('customTypeNameMap', () => {
  it('keys the registry by the wire kind and tolerates an unloaded list', () => {
    expect(customTypeNameMap([{ id: 'abc', name: 'Linter' }]).get('custom_abc')).toBe('Linter');
    expect(customTypeNameMap(undefined).size).toBe(0);
  });
});

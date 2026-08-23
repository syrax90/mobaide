import { describe, expect, it } from 'vitest';
import { sessionResponseSchema, type SessionResponse } from '../../protocol/dto/session';
import {
  attentionChip,
  canStopSession,
  customTypeNameMap,
  sessionDialogLabel,
  sessionHeaderLabel,
  sessionKindLabel,
  sessionStateTone,
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

describe('canStopSession', () => {
  it('offers Stop only while there is something to stop', () => {
    expect(canStopSession('running')).toBe(true);
    expect(canStopSession('starting')).toBe(true);
    expect(canStopSession('stopping')).toBe(false);
    expect(canStopSession('stopped')).toBe(false);
    expect(canStopSession('crashed')).toBe(false);
  });
});

describe('customTypeNameMap', () => {
  it('keys the registry by the wire kind and tolerates an unloaded list', () => {
    expect(customTypeNameMap([{ id: 'abc', name: 'Linter' }]).get('custom_abc')).toBe('Linter');
    expect(customTypeNameMap(undefined).size).toBe(0);
  });
});

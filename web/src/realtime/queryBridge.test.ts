import { QueryClient, type QueryKey } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  applyAgentEvent,
  applyUserEvent,
  refreshOnAgentEventsConnect,
  refreshOnUserEventsConnect,
} from './queryBridge';
import { useSessionsSyncStore } from './sessionsSyncStore';

let client: QueryClient;
let invalidated: QueryKey[];

beforeEach(() => {
  client = new QueryClient();
  useSessionsSyncStore.setState({ snapshots: {} });
  invalidated = [];
  vi.spyOn(client, 'invalidateQueries').mockImplementation((filters) => {
    invalidated.push(filters?.queryKey ?? []);
    return Promise.resolve();
  });
});

function frame(type: string, payload: unknown): string {
  return JSON.stringify({ type, payload });
}

const template = {
  template_id: 't1',
  name: 'Deploy',
  body: 'make deploy',
  is_favorite: false,
  last_used_at: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  version: 3,
};

describe('applyUserEvent', () => {
  it('refetches the shortcut list when a custom agent type is deleted', () => {
    // The server soft-deletes that type's shortcuts in the same transaction and
    // sends no companion shortcut.deleted events, so the shortcut list would
    // keep rendering dead rows if only its own key were invalidated.
    const outcome = applyUserEvent(client, frame('custom_type.deleted', { id: 'ab12cd34ef56' }));

    expect(outcome).toEqual({
      kind: 'invalidated',
      type: 'custom_type.deleted',
      keys: [['custom-agent-types'], ['shortcuts']],
    });
    expect(invalidated).toEqual([['custom-agent-types'], ['shortcuts']]);
  });

  it('invalidates the list a well-formed payload belongs to', () => {
    const outcome = applyUserEvent(client, frame('template.updated', template));

    expect(outcome).toEqual({
      kind: 'invalidated',
      type: 'template.updated',
      keys: [['templates']],
    });
  });

  it('drops a payload its schema rejects instead of invalidating', () => {
    const outcome = applyUserEvent(client, frame('template.created', { template_id: 't1' }));

    expect(outcome).toEqual({ kind: 'dropped' });
    expect(invalidated).toEqual([]);
  });

  it('ignores a well-formed envelope with an unrecognised type', () => {
    const outcome = applyUserEvent(client, frame('template.archived', template));

    expect(outcome).toEqual({ kind: 'ignored', type: 'template.archived' });
    expect(invalidated).toEqual([]);
  });

  it.each(['constructor', 'toString', '__proto__', 'hasOwnProperty'])(
    'ignores %s rather than resolving it off Object.prototype',
    (type) => {
      // `type` is wire data. A bare index lookup on the rules record would hand
      // back an inherited function here and throw out of the message listener.
      expect(applyUserEvent(client, frame(type, template))).toEqual({
        kind: 'ignored',
        type,
      });
      expect(invalidated).toEqual([]);
    },
  );

  it('drops a frame that is not a usable envelope', () => {
    expect(applyUserEvent(client, 'not json at all')).toEqual({ kind: 'dropped' });
    // `payload` absent is a drop on every client, not an empty payload.
    expect(applyUserEvent(client, JSON.stringify({ type: 'template.created' }))).toEqual({
      kind: 'dropped',
    });
    expect(invalidated).toEqual([]);
  });

  it('routes an attention notice to the session list of the stamped agent', () => {
    const attention = {
      session_id: 's1',
      reason: 'permission',
      title: null,
      project_name: null,
      exit_reason: null,
      agent_id: 'a1',
    };

    const outcome = applyUserEvent(client, frame('session.attention', attention));

    expect(outcome).toEqual({
      kind: 'invalidated',
      type: 'session.attention',
      keys: [['agent', 'a1', 'sessions']],
    });
  });

  it('invalidates nothing for an attention notice with no agent to attribute it to', () => {
    const outcome = applyUserEvent(
      client,
      frame('session.attention', { session_id: 's1', reason: 'permission' }),
    );

    expect(outcome).toEqual({ kind: 'invalidated', type: 'session.attention', keys: [] });
    expect(invalidated).toEqual([]);
  });
});

describe('applyAgentEvent', () => {
  it('maps each event family to its agent-scoped key', () => {
    // `sessions.changed`, `projects.changed` and both Auto Scan list events are
    // patched straight into the cache (setQueryData, not invalidateQueries) —
    // each is a complete snapshot, so there is no delta that could drift.
    // `git.changed` is the one pointer left, scoped to the project it names.
    applyAgentEvent(client, 'a1', frame('sessions.changed', {}));
    const projectsOutcome = applyAgentEvent(
      client,
      'a1',
      frame('projects.changed', { projects: [{ name: 'p1', path: '/srv/p1' }] }),
    );
    applyAgentEvent(client, 'a1', frame('git.changed', { project_name: 'alpha' }));

    expect(projectsOutcome).toEqual({
      kind: 'patched',
      type: 'projects.changed',
      keys: [['agent', 'a1', 'projects']],
    });
    expect(invalidated).toEqual([['agent', 'a1', 'git', 'alpha']]);
  });

  it('writes the Auto Scan list and one scan row instead of invalidating', () => {
    const scan = {
      project_name: 'p1',
      status: 'scanning',
      started_at: 1,
      last_activity_at: 1,
    };

    const listOutcome = applyAgentEvent(
      client,
      'a1',
      frame('autoscan.list.changed', { scans: [scan], capacity: 1, max_scans: 5 }),
    );
    const statusOutcome = applyAgentEvent(
      client,
      'a1',
      frame('autoscan.status', { ...scan, status: 'error' }),
    );

    expect(listOutcome).toEqual({
      kind: 'patched',
      type: 'autoscan.list.changed',
      keys: [['agent', 'a1', 'autoscan']],
    });
    expect(statusOutcome).toEqual({
      kind: 'patched',
      type: 'autoscan.status',
      keys: [['agent', 'a1', 'autoscan']],
    });
    expect(invalidated).toEqual([]);
  });

  it('reports no cache write for a status naming a scan the list does not carry', () => {
    const outcome = applyAgentEvent(
      client,
      'a1',
      frame('autoscan.status', {
        project_name: 'unknown',
        status: 'idle',
        started_at: 1,
        last_activity_at: 1,
      }),
    );

    expect(outcome).toEqual({ kind: 'patched', type: 'autoscan.status', keys: [] });
  });

  it('ignores autoscan.event rather than invalidating once per log line', () => {
    // `AutoScanInstance._emit` publishes one of these per scan event, so the
    // pre-WEB-10 invalidation cost a `/proxy` GET per line of output. The detail
    // screen reads this stream from its own `ws/autoscan` channel instead.
    const outcome = applyAgentEvent(client, 'a1', frame('autoscan.event', {}));

    expect(outcome).toEqual({ kind: 'ignored', type: 'autoscan.event' });
    expect(invalidated).toEqual([]);
  });

  it('marks the list synced on a snapshot, and only on one it could apply', () => {
    // The flag is what turns "not in the list" into "this session is gone", so
    // a payload the schema rejected must not arm it. An *empty* snapshot must,
    // though: no sessions at all is exactly when that decision matters.
    applyAgentEvent(client, 'a1', frame('sessions.changed', {}));
    expect(useSessionsSyncStore.getState().snapshots['a1']).toBeUndefined();

    applyAgentEvent(client, 'a1', frame('sessions.changed', { sessions: [] }));
    expect(useSessionsSyncStore.getState().snapshots['a1']).toBe(true);
  });

  it('ignores an unrecognised type without touching the cache', () => {
    const outcome = applyAgentEvent(client, 'a1', frame('sessions.teleported', {}));

    expect(outcome).toEqual({ kind: 'ignored', type: 'sessions.teleported' });
    expect(invalidated).toEqual([]);
  });
});

describe('refresh on connect', () => {
  it('reconciles the account lists and re-probes the version gate', () => {
    refreshOnUserEventsConnect(client);

    // server-info is pinned at staleTime: Infinity and 4002 never reaches a
    // user socket, so reconnect is the only thing that re-evaluates the gate.
    expect(invalidated).toEqual([
      ['server-info'],
      ['agents'],
      ['templates'],
      ['shortcuts'],
      ['custom-agent-types'],
    ]);
  });

  it('reconciles the REST-backed lists of the reconnected agent', () => {
    // Sessions and projects are deliberately excluded: the agent re-pushes an
    // authoritative snapshot of both on every connect (the relay's
    // `request_events_snapshot`), so an extra REST GET here would only race
    // it — and a slow response landing afterwards would roll a list backwards.
    // Git items get no such push at all. Auto Scan gets one on the direct
    // `ws/events` path but *not* through the relay, which is the only path a
    // browser takes (CLA-249) — so it needs reconciling for a different reason.
    refreshOnAgentEventsConnect(client, 'a1');

    expect(invalidated).toEqual([
      ['agent', 'a1', 'git'],
      ['agent', 'a1', 'autoscan'],
    ]);
  });
});

import { QueryClient, type QueryKey } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  applyAgentEvent,
  applyUserEvent,
  refreshOnAgentEventsConnect,
  refreshOnUserEventsConnect,
} from './queryBridge';

let client: QueryClient;
let invalidated: QueryKey[];

beforeEach(() => {
  client = new QueryClient();
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
    // `sessions.changed` is patched straight into the cache via sessionCache.ts
    // (setQueryData, not invalidateQueries) — it is a complete snapshot, so
    // there is no delta that could drift. Only the remaining families still
    // invalidate.
    applyAgentEvent(client, 'a1', frame('sessions.changed', {}));
    applyAgentEvent(client, 'a1', frame('git.changed', {}));
    applyAgentEvent(client, 'a1', frame('autoscan.event', {}));

    expect(invalidated).toEqual([
      ['agent', 'a1', 'projects'],
      ['agent', 'a1', 'autoscan'],
    ]);
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
    // Sessions are deliberately excluded: the agent re-pushes an authoritative
    // `sessions.changed` snapshot on every connect, so an extra REST GET here
    // would only race it (and a slow response landing afterwards would roll
    // the list backwards). Projects still need it since `projects.changed`
    // only fires on an actual change.
    refreshOnAgentEventsConnect(client, 'a1');

    expect(invalidated).toEqual([['agent', 'a1', 'projects']]);
  });
});

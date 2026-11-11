import { QueryClient } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { queryKeys } from '../lib/queryKeys';
import { sessionResponseSchema, type SessionResponse } from '../protocol/dto/session';
import { setTerminalEvictor } from '../terminal/terminalEviction';
import {
  applySessionsSnapshot,
  applySessionUpsert,
  removedSessionIds,
  upsertSession,
} from './sessionCache';

const AGENT = 'agent-1';
const key = queryKeys.sessions(AGENT);

const row = (sessionId: string, over: Record<string, unknown> = {}): SessionResponse =>
  sessionResponseSchema.parse({
    session_id: sessionId,
    state: 'running',
    created_at: '2026-08-04T10:00:00Z',
    ...over,
  });

let client: QueryClient;
let evicted: { agentId: string; sessionIds: readonly string[] }[];

beforeEach(() => {
  client = new QueryClient();
  evicted = [];
  setTerminalEvictor((agentId, sessionIds) => {
    evicted.push({ agentId, sessionIds });
  });
});

afterEach(() => {
  setTerminalEvictor(null);
  client.clear();
});

describe('upsertSession', () => {
  it('replaces a row in place rather than reordering the list', () => {
    const list = [row('a'), row('b'), row('c')];

    const next = upsertSession(list, row('b', { state: 'stopped' }));

    expect(next.map((session) => session.session_id)).toEqual(['a', 'b', 'c']);
    expect(next[1]?.state).toBe('stopped');
    expect(list[1]?.state).toBe('running');
  });

  it('appends a row the list has never seen', () => {
    expect(upsertSession([row('a')], row('b')).map((s) => s.session_id)).toEqual(['a', 'b']);
  });
});

describe('removedSessionIds', () => {
  it('names the ids the authoritative list dropped', () => {
    expect(removedSessionIds([row('a'), row('b')], [row('b'), row('c')])).toEqual(['a']);
    expect(removedSessionIds([], [row('a')])).toEqual([]);
  });
});

describe('applySessionsSnapshot', () => {
  it('seeds a cache nothing has fetched, so a later mount paints with no request', () => {
    expect(applySessionsSnapshot(client, AGENT, { sessions: [{ ...row('a') }] })).toBe('applied');
    expect(client.getQueryData<SessionResponse[]>(key)?.map((s) => s.session_id)).toEqual(['a']);
    // A seed has nothing to remove, so nothing may be evicted from it.
    expect(evicted).toEqual([]);
  });

  it('evicts the terminals of sessions the snapshot no longer lists', () => {
    client.setQueryData<SessionResponse[]>(key, [row('a'), row('b')]);

    expect(applySessionsSnapshot(client, AGENT, { sessions: [{ ...row('b') }] })).toBe('applied');
    expect(evicted).toEqual([{ agentId: AGENT, sessionIds: ['a'] }]);
    expect(client.getQueryData<SessionResponse[]>(key)?.map((s) => s.session_id)).toEqual(['b']);
  });

  it('preserves keys a newer agent added to the row', () => {
    applySessionsSnapshot(client, AGENT, {
      sessions: [{ ...row('a'), future_field: 'keep me' }],
    });

    expect(client.getQueryData<SessionResponse[]>(key)?.[0]).toMatchObject({
      future_field: 'keep me',
    });
  });

  it('drops a malformed payload instead of throwing inside the socket listener', () => {
    expect(applySessionsSnapshot(client, AGENT, { sessions: 'nope' })).toBe('dropped');
    expect(applySessionsSnapshot(client, AGENT, {})).toBe('dropped');
    expect(client.getQueryData(key)).toBeUndefined();
  });
});

describe('applySessionUpsert', () => {
  it('writes nothing at all into a cold cache', () => {
    // A one-row list would masquerade as the whole list. TanStack skips
    // creating the entry when the updater returns undefined, and that
    // behaviour is what this relies on.
    expect(applySessionUpsert(client, AGENT, { session: { ...row('a') } })).toBe('skipped');
    expect(client.getQueryData(key)).toBeUndefined();
  });

  it('upserts into a list that already exists', () => {
    client.setQueryData<SessionResponse[]>(key, [row('a'), row('b')]);

    expect(
      applySessionUpsert(client, AGENT, {
        session: { ...row('b'), attention: 'awaiting_permission' },
      }),
    ).toBe('applied');
    expect(client.getQueryData<SessionResponse[]>(key)?.[1]?.attention).toBe(
      'awaiting_permission',
    );
  });

  it('appends a session the list has not seen yet', () => {
    client.setQueryData<SessionResponse[]>(key, [row('a')]);

    applySessionUpsert(client, AGENT, { session: { ...row('new') } });

    expect(client.getQueryData<SessionResponse[]>(key)?.map((s) => s.session_id)).toEqual([
      'a',
      'new',
    ]);
  });

  it('never evicts a terminal, however the list changed', () => {
    client.setQueryData<SessionResponse[]>(key, [row('a')]);

    applySessionUpsert(client, AGENT, { session: { ...row('a'), state: 'stopped' } });

    expect(evicted).toEqual([]);
  });
});

import { QueryClient, type QueryKey } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionGitStatus } from '../api/sessionGitRepository';
import { queryKeys } from '../lib/queryKeys';
import { sessionResponseSchema, type SessionResponse } from '../protocol/dto/session';
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
  it('refetches the custom type list when a custom agent type is deleted', () => {
    const outcome = applyUserEvent(client, frame('custom_type.deleted', { id: 'ab12cd34ef56' }));

    expect(outcome).toEqual({
      kind: 'invalidated',
      type: 'custom_type.deleted',
      keys: [['custom-agent-types']],
    });
    expect(invalidated).toEqual([['custom-agent-types']]);
  });

  it('refetches the agent list when the account pairs its first agent', () => {
    const outcome = applyUserEvent(
      client,
      frame('account.first_agent_paired', { agent_id: 'a1b2c3d4e5f6' }),
    );

    expect(outcome).toEqual({
      kind: 'invalidated',
      type: 'account.first_agent_paired',
      keys: [['agents']],
    });
    expect(invalidated).toEqual([['agents']]);
  });

  it('drops a first-pairing notice that names no agent', () => {
    const outcome = applyUserEvent(client, frame('account.first_agent_paired', {}));

    expect(outcome).toEqual({ kind: 'dropped' });
    expect(invalidated).toEqual([]);
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

  const shortcut = {
    shortcut_id: 'sc1',
    kind: 'claude',
    chord_token: 'ctrl+alt+d',
    name: 'Deploy',
    position: 0,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    version: 1,
  };

  it.each([
    ['shortcut.created', shortcut],
    ['shortcut.updated', { ...shortcut, version: 2 }],
    ['shortcut.deleted', { shortcut_id: 'sc1', version: 3, updated_at: '2026-01-01T00:00:00Z' }],
  ])('ignores %s now that web keeps no shortcut cache', (type, payload) => {
    // Not a hypothetical unknown type: mobile clients still manage shortcuts,
    // so the server keeps broadcasting this trio to every user socket — web's
    // included. With the rules removed they must take the same unknown-type
    // path as any future event: acknowledged as ignored, nothing invalidated.
    expect(applyUserEvent(client, frame(type, payload))).toEqual({ kind: 'ignored', type });
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

describe('applyAgentEvent git.changed', () => {
  // Android `GitRepository.matchesSession`, read off the query cache: a viewer
  // with no repo answer yet always refreshes; otherwise the event has to name
  // its session's project, or its resolved toplevel / common dir. Every match
  // is the viewer's `sessionGit` prefix, so an open diff is re-read with the
  // list rather than left describing a tree that has moved.
  const gitStatus = (over: Partial<SessionGitStatus> = {}): SessionGitStatus => ({
    session_id: 's1',
    etag: 'e1',
    unchanged: false,
    unavailable_reason: null,
    repo: {
      toplevel: '/srv/p1',
      git_dir: '/srv/p1/.git',
      common_dir: '/srv/p1/.git',
      head: { branch: 'main', detached: false, initial: false },
      state: 'clean',
      is_linked_worktree: false,
    },
    entries: [],
    truncated: false,
    entry_limit: 5000,
    ...over,
  });

  const sessionRow = (sessionId: string, projectName: string): SessionResponse =>
    sessionResponseSchema.parse({
      session_id: sessionId,
      state: 'running',
      created_at: '2026-08-04T10:00:00Z',
      project_name: projectName,
    });

  const viewer = (sessionId: string, status: SessionGitStatus): void => {
    client.setQueryData(queryKeys.sessionGitStatus('a1', sessionId), status);
  };

  const projectKey = ['agent', 'a1', 'git', 'p1'];
  const viewerKey = (sessionId: string): QueryKey => ['agent', 'a1', 'session-git', sessionId];

  it('refreshes a viewer that has no repo answer yet, whatever the event names', () => {
    // Loaded but unavailable, and still loading: neither can be matched by
    // repo, and either may be about to become one.
    viewer('s1', gitStatus({ repo: null, unavailable_reason: 'not_a_repo' }));
    client.getQueryCache().build(client, { queryKey: queryKeys.sessionGitStatus('a1', 's2') });

    const outcome = applyAgentEvent(
      client,
      'a1',
      frame('git.changed', { project_name: 'p1', toplevel: '/elsewhere', common_dir: null }),
    );

    expect(outcome).toEqual({
      kind: 'invalidated',
      type: 'git.changed',
      keys: [projectKey, viewerKey('s1'), viewerKey('s2')],
    });
    expect(invalidated).toEqual([projectKey, viewerKey('s1'), viewerKey('s2')]);
  });

  it("matches a viewer by the project's resolved toplevel", () => {
    viewer('s1', gitStatus());

    applyAgentEvent(
      client,
      'a1',
      frame('git.changed', { project_name: 'p1', toplevel: '/srv/p1' }),
    );

    expect(invalidated).toEqual([projectKey, viewerKey('s1')]);
  });

  it('matches a linked worktree by the common dir it shares with its project', () => {
    viewer(
      's1',
      gitStatus({
        repo: {
          toplevel: '/wt/feature',
          git_dir: '/srv/p1/.git/worktrees/feature',
          common_dir: '/srv/p1/.git',
          head: { branch: 'feature', detached: false, initial: false },
          state: 'clean',
          is_linked_worktree: true,
        },
      }),
    );

    applyAgentEvent(
      client,
      'a1',
      frame('git.changed', {
        project_name: 'p1',
        toplevel: '/srv/p1',
        common_dir: '/srv/p1/.git',
      }),
    );

    expect(invalidated).toEqual([projectKey, viewerKey('s1')]);
  });

  it("matches by the session row's project name when the event carries no paths", () => {
    client.setQueryData(queryKeys.sessions('a1'), [sessionRow('s1', 'p1')]);
    viewer('s1', gitStatus({ repo: { ...gitStatus().repo!, toplevel: '/elsewhere' } }));

    applyAgentEvent(client, 'a1', frame('git.changed', { project_name: 'p1' }));

    expect(invalidated).toEqual([projectKey, viewerKey('s1')]);
  });

  it('leaves a viewer on another repository alone', () => {
    client.setQueryData(queryKeys.sessions('a1'), [sessionRow('s1', 'p2')]);
    viewer(
      's1',
      gitStatus({
        repo: { ...gitStatus().repo!, toplevel: '/srv/p2', common_dir: '/srv/p2/.git' },
      }),
    );

    const outcome = applyAgentEvent(
      client,
      'a1',
      frame('git.changed', {
        project_name: 'p1',
        toplevel: '/srv/p1',
        common_dir: '/srv/p1/.git',
      }),
    );

    expect(outcome).toEqual({ kind: 'invalidated', type: 'git.changed', keys: [projectKey] });
    expect(invalidated).toEqual([projectKey]);
  });

  it('does not let a null path on the wire match anything', () => {
    // `toplevel: null` is "no longer a repo"; only an absent-or-null compare
    // against a real path could match, and a cached repo always has one.
    viewer('s1', gitStatus());

    applyAgentEvent(
      client,
      'a1',
      frame('git.changed', { project_name: 'other', toplevel: null, common_dir: null }),
    );

    expect(invalidated).toEqual([['agent', 'a1', 'git', 'other']]);
  });

  it('matches on the status poll only, never on a stray cached diff', () => {
    // A diff left in the cache after its poll was garbage-collected names no
    // repo; it is refreshed when a live poll matches, not on its own.
    client.setQueryData(
      queryKeys.sessionGitDiff('a1', 's1', { path: 'a', scope: 'unstaged', context: 3 }),
      { path: 'a', scope: 'unstaged', kind: 'text' },
    );

    applyAgentEvent(client, 'a1', frame('git.changed', { project_name: 'p1' }));

    expect(invalidated).toEqual([projectKey]);
  });

  it('drops a git.changed that names no project', () => {
    viewer('s1', gitStatus({ repo: null }));

    const outcome = applyAgentEvent(client, 'a1', frame('git.changed', { toplevel: '/srv/p1' }));

    expect(outcome).toEqual({ kind: 'dropped' });
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
    // The in-session Changes viewers are git items' case again: `git.changed`
    // is their only push and it carries no snapshot.
    refreshOnAgentEventsConnect(client, 'a1');

    expect(invalidated).toEqual([
      ['agent', 'a1', 'git'],
      ['agent', 'a1', 'session-git'],
      ['agent', 'a1', 'autoscan'],
    ]);
  });
});

describe('applyAgentEvent git jobs', () => {
  // Both frames carry a whole `GitJobResponse`, so — like the Auto Scan pair —
  // they are written, never invalidated: the agent emits up to four a second
  // while a push runs, and a `/proxy` GET per frame is the storm its throttle
  // exists to prevent.
  const jobFrame = (over: Record<string, unknown> = {}) => ({
    job_id: 'j1',
    session_id: 's1',
    op: 'push',
    status: 'running',
    phase: null,
    percent: null,
    line: null,
    ok: null,
    error_code: null,
    message: null,
    ...over,
  });
  const jobKey = queryKeys.sessionGitJob('a1', 's1', 'j1');

  it('writes a progress frame and the session pointer instead of invalidating', () => {
    const outcome = applyAgentEvent(
      client,
      'a1',
      frame('git.job.progress', jobFrame({ phase: 'Writing objects', percent: 45 })),
    );

    expect(outcome).toEqual({ kind: 'patched', type: 'git.job.progress', keys: [jobKey] });
    expect(client.getQueryData(jobKey)).toMatchObject({ phase: 'Writing objects', percent: 45 });
    expect(client.getQueryData(queryKeys.sessionGitLatestJob('a1', 's1'))).toEqual({ jobId: 'j1' });
    expect(invalidated).toEqual([]);
  });

  it('writes the done frame over the running one and leaves the tree refresh to git.changed', () => {
    applyAgentEvent(client, 'a1', frame('git.job.progress', jobFrame({ percent: 45 })));

    const outcome = applyAgentEvent(
      client,
      'a1',
      frame('git.job.done', jobFrame({ status: 'done', ok: true, message: 'main -> main' })),
    );

    expect(outcome).toEqual({ kind: 'patched', type: 'git.job.done', keys: [jobKey] });
    expect(client.getQueryData(jobKey)).toMatchObject({
      status: 'done',
      ok: true,
      message: 'main -> main',
    });
    expect(invalidated).toEqual([]);
  });

  it('drops a job frame it cannot type', () => {
    const outcome = applyAgentEvent(client, 'a1', frame('git.job.progress', jobFrame({ op: 'clone' })));

    expect(outcome).toEqual({ kind: 'dropped' });
    expect(client.getQueryData(jobKey)).toBeUndefined();
    expect(invalidated).toEqual([]);
  });
});

describe('applyAgentEvent voice.changed', () => {
  // A whole `VoiceStatusResponse` per frame, throttled to two a second while
  // a download runs: written, never invalidated.
  const voiceFrame = (over: Record<string, unknown> = {}) => ({
    state: 'downloading',
    enabled: true,
    repairing: false,
    model: {
      id: 'parakeet',
      name: 'Parakeet',
      languages: ['en'],
      download_bytes: 100,
      disk_bytes: 200,
      ram_bytes: 300,
      notice: 'CC-BY-4.0',
    },
    progress: { done_bytes: 43, total_bytes: 100 },
    host: {
      ram_total_bytes: null,
      disk_free_bytes: null,
      cpu_count: null,
      recommendation: 'ok',
      reasons: [],
    },
    speed_factor: null,
    error: null,
    ...over,
  });
  const voiceKey = queryKeys.voiceStatus('a1');

  it('writes the snapshot for that agent instead of invalidating', () => {
    applyAgentEvent(client, 'a1', frame('voice.changed', voiceFrame()));

    const outcome = applyAgentEvent(
      client,
      'a1',
      frame('voice.changed', voiceFrame({ progress: { done_bytes: 87, total_bytes: 100 } })),
    );

    expect(outcome).toEqual({ kind: 'patched', type: 'voice.changed', keys: [voiceKey] });
    expect(client.getQueryData(voiceKey)).toMatchObject({
      state: 'downloading',
      progress: { done_bytes: 87, total_bytes: 100 },
    });
    expect(client.getQueryData(queryKeys.voiceStatus('a2'))).toBeUndefined();
    expect(invalidated).toEqual([]);
  });

  it('keeps a state this client does not know, for the mic to read as unavailable', () => {
    applyAgentEvent(client, 'a1', frame('voice.changed', voiceFrame({ state: 'paused' })));

    expect(client.getQueryData(voiceKey)).toMatchObject({ state: 'paused' });
  });

  it('drops a frame it cannot type and keeps the last good one', () => {
    applyAgentEvent(client, 'a1', frame('voice.changed', voiceFrame({ state: 'ready' })));

    const outcome = applyAgentEvent(client, 'a1', frame('voice.changed', voiceFrame({ enabled: 'yes' })));

    expect(outcome).toEqual({ kind: 'dropped' });
    expect(client.getQueryData(voiceKey)).toMatchObject({ state: 'ready' });
    expect(invalidated).toEqual([]);
  });
});

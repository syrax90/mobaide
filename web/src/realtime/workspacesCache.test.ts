import { QueryClient } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { queryKeys } from '../lib/queryKeys';
import { workspaceInfoSchema, type WorkspaceInfo } from '../protocol/dto/workspaces';
import { applyWorkspacesSnapshot, upsertWorkspace } from './workspacesCache';

const AGENT = 'agent-1';
const key = queryKeys.workspaces(AGENT);

const workspace = (name: string, order: number): WorkspaceInfo =>
  workspaceInfoSchema.parse({ name, path: `/srv/${name}`, order });

const cached = (client: QueryClient): string[] =>
  (client.getQueryData<WorkspaceInfo[]>(key) ?? []).map((entry) => entry.name);

let client: QueryClient;

beforeEach(() => {
  client = new QueryClient();
});

afterEach(() => {
  client.clear();
});

describe('applyWorkspacesSnapshot', () => {
  it('seeds a cache nothing has fetched, in server order', () => {
    expect(
      applyWorkspacesSnapshot(client, AGENT, {
        projects: [workspace('c', 2), workspace('a', 0), workspace('b', 1)],
      }),
    ).toBe('applied');

    expect(cached(client)).toEqual(['a', 'b', 'c']);
  });

  it('breaks an order tie by name, so two equal ranks do not swap between pushes', () => {
    applyWorkspacesSnapshot(client, AGENT, { projects: [workspace('zeta', 0), workspace('alpha', 0)] });

    expect(cached(client)).toEqual(['alpha', 'zeta']);
  });

  it('preserves keys a newer agent added to a row', () => {
    applyWorkspacesSnapshot(client, AGENT, {
      projects: [{ ...workspace('a', 0), future_field: 'keep me' }],
    });

    expect(client.getQueryData<WorkspaceInfo[]>(key)?.[0]).toMatchObject({
      future_field: 'keep me',
    });
  });

  it('drops a malformed payload instead of throwing inside the socket listener', () => {
    client.setQueryData<WorkspaceInfo[]>(key, [workspace('a', 0)]);

    expect(applyWorkspacesSnapshot(client, AGENT, { projects: 'nope' })).toBe('dropped');
    expect(applyWorkspacesSnapshot(client, AGENT, {})).toBe('dropped');
    expect(applyWorkspacesSnapshot(client, AGENT, { projects: [{ path: '/no-name' }] })).toBe(
      'dropped',
    );
    expect(cached(client)).toEqual(['a']);
  });

  it('survives a REST list that resolves after it with older rows', async () => {
    // `listWorkspaces` and the events channel start together on mount, so the GET
    // can land second. Cancelling first is what keeps the snapshot on top.
    let settleList: (workspaces: WorkspaceInfo[]) => void = () => undefined;
    const listed = new Promise<WorkspaceInfo[]>((resolve) => {
      settleList = resolve;
    });
    const fetched = client
      .fetchQuery({ queryKey: key, queryFn: () => listed })
      .catch(() => undefined);

    applyWorkspacesSnapshot(client, AGENT, { projects: [workspace('fresh', 0)] });
    settleList([workspace('stale', 0)]);
    await fetched;
    await listed;

    expect(cached(client)).toEqual(['fresh']);
  });
});

describe('upsertWorkspace', () => {
  it('adds a workspace the list does not hold, in server order', () => {
    const next = upsertWorkspace([workspace('a', 0), workspace('c', 2)], workspace('b', 1));

    expect(next.map((entry) => entry.name)).toEqual(['a', 'b', 'c']);
  });

  it('replaces the same-named entry instead of listing it twice', () => {
    // The usual case after a create: the dialog's refetch has already brought
    // the workspace in, and the seed must not add a second row under its name.
    const moved = workspaceInfoSchema.parse({ name: 'a', path: '/srv/moved', order: 3 });

    const next = upsertWorkspace([workspace('a', 0), workspace('b', 1)], moved);

    expect(next.map((entry) => entry.name)).toEqual(['b', 'a']);
    expect(next.at(1)?.path).toBe('/srv/moved');
  });
});

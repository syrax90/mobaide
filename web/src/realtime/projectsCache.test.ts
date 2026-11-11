import { QueryClient } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { queryKeys } from '../lib/queryKeys';
import { projectInfoSchema, type ProjectInfo } from '../protocol/dto/projects';
import { applyProjectsSnapshot, upsertProject } from './projectsCache';

const AGENT = 'agent-1';
const key = queryKeys.projects(AGENT);

const project = (name: string, order: number): ProjectInfo =>
  projectInfoSchema.parse({ name, path: `/srv/${name}`, order });

const cached = (client: QueryClient): string[] =>
  (client.getQueryData<ProjectInfo[]>(key) ?? []).map((entry) => entry.name);

let client: QueryClient;

beforeEach(() => {
  client = new QueryClient();
});

afterEach(() => {
  client.clear();
});

describe('applyProjectsSnapshot', () => {
  it('seeds a cache nothing has fetched, in server order', () => {
    expect(
      applyProjectsSnapshot(client, AGENT, {
        projects: [project('c', 2), project('a', 0), project('b', 1)],
      }),
    ).toBe('applied');

    expect(cached(client)).toEqual(['a', 'b', 'c']);
  });

  it('breaks an order tie by name, so two equal ranks do not swap between pushes', () => {
    applyProjectsSnapshot(client, AGENT, { projects: [project('zeta', 0), project('alpha', 0)] });

    expect(cached(client)).toEqual(['alpha', 'zeta']);
  });

  it('preserves keys a newer agent added to a row', () => {
    applyProjectsSnapshot(client, AGENT, {
      projects: [{ ...project('a', 0), future_field: 'keep me' }],
    });

    expect(client.getQueryData<ProjectInfo[]>(key)?.[0]).toMatchObject({
      future_field: 'keep me',
    });
  });

  it('drops a malformed payload instead of throwing inside the socket listener', () => {
    client.setQueryData<ProjectInfo[]>(key, [project('a', 0)]);

    expect(applyProjectsSnapshot(client, AGENT, { projects: 'nope' })).toBe('dropped');
    expect(applyProjectsSnapshot(client, AGENT, {})).toBe('dropped');
    expect(applyProjectsSnapshot(client, AGENT, { projects: [{ path: '/no-name' }] })).toBe(
      'dropped',
    );
    expect(cached(client)).toEqual(['a']);
  });

  it('survives a REST list that resolves after it with older rows', async () => {
    // `listProjects` and the events channel start together on mount, so the GET
    // can land second. Cancelling first is what keeps the snapshot on top.
    let settleList: (projects: ProjectInfo[]) => void = () => undefined;
    const listed = new Promise<ProjectInfo[]>((resolve) => {
      settleList = resolve;
    });
    const fetched = client
      .fetchQuery({ queryKey: key, queryFn: () => listed })
      .catch(() => undefined);

    applyProjectsSnapshot(client, AGENT, { projects: [project('fresh', 0)] });
    settleList([project('stale', 0)]);
    await fetched;
    await listed;

    expect(cached(client)).toEqual(['fresh']);
  });
});

describe('upsertProject', () => {
  it('adds a project the list does not hold, in server order', () => {
    const next = upsertProject([project('a', 0), project('c', 2)], project('b', 1));

    expect(next.map((entry) => entry.name)).toEqual(['a', 'b', 'c']);
  });

  it('replaces the same-named entry instead of listing it twice', () => {
    // The usual case after a create: the dialog's refetch has already brought
    // the project in, and the seed must not add a second row under its name.
    const moved = projectInfoSchema.parse({ name: 'a', path: '/srv/moved', order: 3 });

    const next = upsertProject([project('a', 0), project('b', 1)], moved);

    expect(next.map((entry) => entry.name)).toEqual(['b', 'a']);
    expect(next.at(1)?.path).toBe('/srv/moved');
  });
});

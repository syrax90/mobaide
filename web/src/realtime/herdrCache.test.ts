import { QueryClient } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { queryKeys } from '../lib/queryKeys';
import type { HerdrSnapshot } from '../protocol/dto/herdr';
import { applyHerdrSnapshot } from './herdrCache';
import { applyAgentEvent } from './queryBridge';

const AGENT = 'agent-1';
const key = queryKeys.herdr(AGENT);

const wireServer = (serverId: string, paneIds: string[] = []) => ({
  server_id: serverId,
  product: 'tui',
  name: 'default',
  state: 'ready',
  workspaces: [
    {
      workspace_id: 'w1',
      label: 'mobaide',
      tabs: [
        {
          tab_id: 'w1:t1',
          label: 'main',
          panes: paneIds.map((pane_id) => ({ pane_id, title: pane_id })),
        },
      ],
    },
  ],
});

function cachedServers(client: QueryClient): string[] {
  return (client.getQueryData<HerdrSnapshot>(key)?.servers ?? []).map((s) => s.server_id);
}

let client: QueryClient;

beforeEach(() => {
  client = new QueryClient();
});

afterEach(() => {
  client.clear();
});

describe('applyHerdrSnapshot', () => {
  it('replaces the cached state with the whole new one', () => {
    applyHerdrSnapshot(client, AGENT, { servers: [wireServer('tui:default'), wireServer('tui:work')] });

    expect(applyHerdrSnapshot(client, AGENT, { servers: [wireServer('core:default')] })).toBe(
      'applied',
    );

    // A stopped server leaves the dashboard: the event is the state, not a patch.
    expect(cachedServers(client)).toEqual(['core:default']);
  });

  it('writes an empty state when the last server stops', () => {
    applyHerdrSnapshot(client, AGENT, { servers: [wireServer('tui:default')] });

    expect(applyHerdrSnapshot(client, AGENT, { servers: [] })).toBe('applied');

    expect(cachedServers(client)).toEqual([]);
  });

  it.each([null, 'servers', 7, [], {}, { servers: null }, { servers: 'none' }, { server: [] }])(
    'drops a payload without a servers array and keeps the cache: %j',
    (payload) => {
      // Every field has a default, so a bare object would parse to an empty
      // dashboard and hide running servers.
      applyHerdrSnapshot(client, AGENT, { servers: [wireServer('tui:default')] });

      expect(applyHerdrSnapshot(client, AGENT, payload)).toBe('dropped');

      expect(cachedServers(client)).toEqual(['tui:default']);
    },
  );

  it('drops a snapshot with a server it cannot decode', () => {
    applyHerdrSnapshot(client, AGENT, { servers: [wireServer('tui:default')] });

    expect(applyHerdrSnapshot(client, AGENT, { servers: [{ name: 'no id' }] })).toBe('dropped');

    expect(cachedServers(client)).toEqual(['tui:default']);
  });

  it('keeps a slower fetch from overwriting the newer event', async () => {
    let resolveFetch: (value: HerdrSnapshot) => void = () => undefined;
    const fetched = client
      .fetchQuery({
        queryKey: key,
        queryFn: () =>
          new Promise<HerdrSnapshot>((resolve) => {
            resolveFetch = resolve;
          }),
      })
      .catch(() => undefined);

    applyHerdrSnapshot(client, AGENT, { servers: [wireServer('tui:new')] });
    resolveFetch({ servers: [] });
    await fetched;

    expect(cachedServers(client)).toEqual(['tui:new']);
  });

  it('writes one agent only', () => {
    applyHerdrSnapshot(client, AGENT, { servers: [wireServer('tui:default')] });

    expect(client.getQueryData(queryKeys.herdr('agent-2'))).toBeUndefined();
  });
});

describe('applyAgentEvent herdr.changed', () => {
  const frame = (payload: unknown) => JSON.stringify({ type: 'herdr.changed', payload });

  it('writes the snapshot instead of invalidating', () => {
    const outcome = applyAgentEvent(
      client,
      AGENT,
      frame({ servers: [wireServer('tui:default', ['w1:p1'])] }),
    );

    expect(outcome).toEqual({ kind: 'patched', type: 'herdr.changed', keys: [key] });
    expect(cachedServers(client)).toEqual(['tui:default']);
    expect(client.getQueryState(key)?.isInvalidated).toBe(false);
  });

  it('reports a payload it cannot use as dropped', () => {
    expect(applyAgentEvent(client, AGENT, frame({}))).toEqual({ kind: 'dropped' });
  });
});

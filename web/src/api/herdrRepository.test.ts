import { afterEach, describe, expect, it, vi } from 'vitest';

// Hand-written stand-in for the typed agent client, as in
// `voiceRepository.test.ts`: what is under test is the repository's own
// request shape and error mapping.
const agent = vi.hoisted(() => ({
  results: [] as { data?: unknown; error?: unknown; response: Response }[],
  requests: [] as { agentId: string; method: string; path: string; init: unknown }[],
}));

vi.mock('./clients', () => ({
  createAgentClient: (agentId: string) => {
    const call = (method: string) => (path: string, init: unknown) => {
      agent.requests.push({ agentId, method, path, init });
      const next = agent.results.shift();
      if (next === undefined) return Promise.reject(new TypeError('Failed to fetch'));
      return Promise.resolve(next);
    };
    return { GET: call('GET'), POST: call('POST') };
  },
}));

const {
  closeHerdrItem,
  fetchHerdr,
  fetchHerdrPaneText,
  HERDR_GONE_CODES,
  herdrErrorCode,
  openHerdrPane,
  setHerdrControl,
} = await import('./herdrRepository');
const { toApiError } = await import('./errors');
const { PANE_CLIENT_ID } = await import('../protocol/paneClientId');

afterEach(() => {
  agent.results.length = 0;
  agent.requests.length = 0;
});

const ok = (data?: unknown) => ({ data, response: new Response(null, { status: 200 }) });
const refusal = (status: number, code: string) => ({
  error: { detail: { code, message: 'This pane is closed.' } },
  response: new Response(null, { status }),
});

const PANE = { serverId: 'core:default', paneId: 'box/t3' };

describe('herdrErrorCode', () => {
  it('reads the code of a Herdr refusal', () => {
    const error = toApiError(new Response(null, { status: 404 }), {
      detail: { code: 'pane_not_found', message: 'This pane is closed.' },
    });

    expect(herdrErrorCode(error)).toBe('pane_not_found');
    expect(HERDR_GONE_CODES).toContain(herdrErrorCode(error));
  });

  it.each([
    toApiError(new Response(null, { status: 502 }), { detail: 'Bad gateway' }),
    toApiError(new Response(null, { status: 422 }), { detail: [{ loc: ['pane_id'], msg: 'x' }] }),
    toApiError(new Response(null, { status: 409 }), { detail: { code: 7 } }),
    new TypeError('Failed to fetch'),
    null,
  ])('has no code for any other error', (error) => {
    expect(herdrErrorCode(error)).toBeNull();
  });
});

describe('herdr requests', () => {
  it('names a pane in the body, never in the path', async () => {
    // A pane id can contain `/` and `:`.
    agent.results.push(ok({ session_id: 'herdr-0123' }), ok({ ok: true }));

    const sessionId = await openHerdrPane('a1', PANE);
    await setHerdrControl('a1', PANE, true);

    expect(sessionId).toBe('herdr-0123');
    expect(agent.requests).toEqual([
      {
        agentId: 'a1',
        method: 'POST',
        path: '/api/herdr/pane/open',
        init: { body: { server_id: 'core:default', pane_id: 'box/t3' } },
      },
      {
        agentId: 'a1',
        method: 'POST',
        path: '/api/herdr/pane/control',
        init: { body: { server_id: 'core:default', pane_id: 'box/t3', control: true, client_id: PANE_CLIENT_ID } },
      },
    ]);
  });

  it('reads pane text with the ids in the query', async () => {
    agent.results.push(ok({ text: 'last line', truncated: true }));

    const text = await fetchHerdrPaneText('a1', PANE, 1000);

    expect(text).toEqual({ text: 'last line', truncated: true });
    expect(agent.requests[0]).toMatchObject({
      method: 'GET',
      path: '/api/herdr/pane/text',
      init: { params: { query: { server_id: 'core:default', pane_id: 'box/t3', lines: 1000 } } },
    });
  });

  it('raises the coded refusal of a pane that is gone', async () => {
    agent.results.push(refusal(404, 'pane_not_found'), refusal(404, 'server_not_found'));

    const opened = await openHerdrPane('a1', PANE).catch((error: unknown) => error);
    const closed = await closeHerdrItem('a1', {
      server_id: 'core:default',
      target: 'pane',
      id: 'box/t3',
    }).catch((error: unknown) => error);

    expect(herdrErrorCode(opened)).toBe('pane_not_found');
    expect(herdrErrorCode(closed)).toBe('server_not_found');
  });

  it('decodes the snapshot through the same schema as the herdr.changed event', async () => {
    agent.results.push(
      ok({ servers: [{ server_id: 'tui:default', version: null, workspaces: [] }] }),
    );

    const snapshot = await fetchHerdr('a1');

    // Defaults filled, nulls dropped: the cache holds one shape from both writers.
    expect(snapshot.servers[0]).toMatchObject({
      server_id: 'tui:default',
      product: 'tui',
      state: 'ready',
      features: { control: false, agents: [] },
    });
    expect(snapshot.servers[0]?.version).toBeUndefined();
  });

  it('rejects a snapshot it cannot decode rather than caching it', async () => {
    agent.results.push(ok({ servers: [{ name: 'no id' }] }));

    await expect(fetchHerdr('a1')).rejects.toThrow();
  });
});

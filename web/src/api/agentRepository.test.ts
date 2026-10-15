import { afterEach, describe, expect, it, vi } from 'vitest';

// Hand-written stand-in for the typed client, as in `templateRepository.test.ts`:
// `relayClient` is built with the SPA's relative base URL, which the undici
// Request under vitest refuses to parse. What the repository hands the client
// is what is under test.
const relay = vi.hoisted(() => ({
  results: [] as { data?: unknown; error?: unknown; response: Response }[],
  calls: [] as { method: string; path: string; options: unknown }[],
}));

function respond(method: string) {
  return (path: string, options?: unknown) => {
    relay.calls.push({ method, path, options });
    const next = relay.results.shift();
    if (next === undefined) return Promise.reject(new Error(`unexpected ${method} ${path}`));
    return Promise.resolve(next);
  };
}

vi.mock('./clients', () => ({
  relayClient: { GET: respond('GET'), DELETE: respond('DELETE') },
}));

const { deleteAgent, listAgents } = await import('./agentRepository');

afterEach(() => {
  relay.results.length = 0;
  relay.calls.length = 0;
});

const noContent = (): Response => new Response(null, { status: 204 });

describe('deleteAgent', () => {
  it('sends no query for a plain remove', async () => {
    relay.results.push({ response: noContent() });

    await deleteAgent('agent-1');

    expect(relay.calls).toEqual([
      {
        method: 'DELETE',
        path: '/api/agents/{agent_id}',
        options: { params: { path: { agent_id: 'agent-1' } } },
      },
    ]);
  });

  it('asks the relay to uninstall when requested', async () => {
    relay.results.push({ response: noContent() });

    await deleteAgent('agent-1', true);

    expect(relay.calls[0]?.options).toEqual({
      params: { path: { agent_id: 'agent-1' }, query: { uninstall: true } },
    });
  });
});

describe('listAgents supportsUninstall', () => {
  const summary = (agentId: string, extra: Record<string, unknown>) => ({
    agent_id: agentId,
    name: agentId,
    online: true,
    created_at: '2026-08-04T10:00:00Z',
    last_seen_at: null,
    ...extra,
  });

  it('maps the flag and defaults it to false when the relay omits it', async () => {
    relay.results.push({
      data: [
        summary('capable', { supports_uninstall: true }),
        summary('older', {}),
      ],
      response: new Response(null, { status: 200 }),
    });

    const agents = await listAgents();

    expect(
      Object.fromEntries(agents.map((agent) => [agent.agentId, agent.supportsUninstall])),
    ).toEqual({ capable: true, older: false });
  });
});

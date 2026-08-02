import { beforeEach, describe, expect, it } from 'vitest';
import { setAgentTokenProvider, setUserTokenProvider } from './auth';
import { createAgentClient, relayClient } from './clients';

// The undici Request that vitest injects into jsdom rejects the relative
// base URLs the clients use in the browser, so every call overrides baseUrl
// with an absolute origin. The bearer middleware under test is unaffected.
const testBaseUrl = 'http://relay.test';

function captureFetch() {
  const requests: Request[] = [];
  const fetch = (request: Request): Promise<Response> => {
    requests.push(request);
    return Promise.resolve(
      new Response('{}', {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  };
  return { requests, fetch };
}

describe('api client auth axes', () => {
  beforeEach(() => {
    setUserTokenProvider(() => null);
    setAgentTokenProvider(() => null);
  });

  it('relayClient attaches the user token as a bearer header', async () => {
    setUserTokenProvider(() => 'user-jwt');
    const { requests, fetch } = captureFetch();

    await relayClient.GET('/api/health', { fetch, baseUrl: testBaseUrl });

    expect(requests).toHaveLength(1);
    expect(requests[0]?.headers.get('Authorization')).toBe('Bearer user-jwt');
  });

  it('relayClient stays unauthenticated when only an agent token exists', async () => {
    setAgentTokenProvider(() => 'agent-jwt');
    const { requests, fetch } = captureFetch();

    await relayClient.GET('/api/health', { fetch, baseUrl: testBaseUrl });

    expect(requests[0]?.headers.get('Authorization')).toBeNull();
  });

  it('agent client attaches the token minted for its bound agentId', async () => {
    setAgentTokenProvider((agentId) => `agent-jwt-${agentId}`);
    const { requests, fetch } = captureFetch();

    await createAgentClient('agent-a').GET('/api/health', {
      fetch,
      baseUrl: testBaseUrl,
    });

    expect(requests).toHaveLength(1);
    expect(requests[0]?.headers.get('Authorization')).toBe(
      'Bearer agent-jwt-agent-a',
    );
  });

  it('agent clients for different agents dispatch to their own tokens', async () => {
    setAgentTokenProvider((agentId) => `agent-jwt-${agentId}`);
    const { requests, fetch } = captureFetch();

    await createAgentClient('agent-a').GET('/api/health', {
      fetch,
      baseUrl: testBaseUrl,
    });
    await createAgentClient('agent-b').GET('/api/health', {
      fetch,
      baseUrl: testBaseUrl,
    });

    expect(requests.map((r) => r.headers.get('Authorization'))).toEqual([
      'Bearer agent-jwt-agent-a',
      'Bearer agent-jwt-agent-b',
    ]);
  });

  it('agent client stays unauthenticated when only a user token exists', async () => {
    setUserTokenProvider(() => 'user-jwt');
    const { requests, fetch } = captureFetch();

    await createAgentClient('agent-a').GET('/api/health', {
      fetch,
      baseUrl: testBaseUrl,
    });

    expect(requests[0]?.headers.get('Authorization')).toBeNull();
  });
});

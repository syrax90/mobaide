import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  resetTokenProviders,
  setAgentTokenProvider,
  setUserTokenProvider,
  setUserTokenRefresher,
} from './auth';
import {
  createAgentClient,
  relayClient,
  setSessionRefresher,
  setUserUnauthorizedHandler,
} from './clients';

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

// `resetTokenProviders` only covers the seams that live in auth.ts, so the two
// registered in clients.ts have to be put back by hand — otherwise a handler
// leaks into every later test in the file.
afterEach(() => {
  resetTokenProviders();
  setSessionRefresher(() => Promise.resolve('terminal'));
  setUserUnauthorizedHandler(null);
  vi.unstubAllGlobals();
});

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

describe('relayClient user session middleware', () => {
  let token: string | null;
  let teardowns: number;

  beforeEach(() => {
    token = 'stale-jwt';
    teardowns = 0;
    setUserTokenProvider(() => token);
    setUserUnauthorizedHandler(() => {
      teardowns += 1;
    });
  });

  function unauthorizedFetch() {
    const requests: Request[] = [];
    const fetch = (request: Request): Promise<Response> => {
      requests.push(request);
      return Promise.resolve(
        new Response('{"detail":"Not authenticated"}', {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        }),
      );
    };
    return { requests, fetch };
  }

  /**
   * The retry leg goes out through the global `fetch`, not the one the call
   * was handed, so it can only be observed from the global.
   */
  function captureReplays() {
    const replays: Request[] = [];
    vi.stubGlobal('fetch', (request: Request) => {
      replays.push(request);
      return Promise.resolve(
        new Response('{}', {
          status: 200,
          headers: { 'Content-Type': 'application/json', 'X-Replay': '1' },
        }),
      );
    });
    return replays;
  }

  it('sends the token the pre-emptive refresh produced, not the one it started with', async () => {
    // openapi-fetch runs onRequest in registration order, so the refresh has to
    // be awaited before the bearer is read; otherwise every rotation would go
    // out one request late and 401.
    setUserTokenRefresher(async () => {
      await Promise.resolve();
      token = 'refreshed-jwt';
    });
    const { requests, fetch } = captureFetch();

    await relayClient.GET('/api/health', { fetch, baseUrl: testBaseUrl });

    expect(requests[0]?.headers.get('Authorization')).toBe('Bearer refreshed-jwt');
  });

  it('replays a 401 once with the rotated token and returns the retry response', async () => {
    setSessionRefresher(() => {
      token = 'fresh-jwt';
      return Promise.resolve('success');
    });
    const { requests, fetch } = unauthorizedFetch();
    const replays = captureReplays();

    const result = await relayClient.GET('/api/health', {
      fetch,
      baseUrl: testBaseUrl,
    });

    expect(requests).toHaveLength(1);
    expect(requests[0]?.headers.get('Authorization')).toBe('Bearer stale-jwt');
    expect(replays).toHaveLength(1);
    expect(replays[0]?.url).toBe(requests[0]?.url);
    expect(replays[0]?.headers.get('Authorization')).toBe('Bearer fresh-jwt');
    expect(replays[0]?.headers.get('X-Mobaide-Retried')).toBe('1');
    expect(result.response.status).toBe(200);
    expect(result.response.headers.get('X-Replay')).toBe('1');
    expect(teardowns).toBe(0);
  });

  it('signs out when the refresh reports success with the same token', async () => {
    // `refreshUserSession` short-circuits to success without a round trip
    // whenever the token is outside the 60s expiry leeway, so every 401 that
    // was never about expiry — an admin unregistering the account, a
    // self-delete — arrives here as success carrying the credential the server
    // just refused. Replaying it would 401 forever, and since the replay
    // bypasses this middleware nothing else would ever end the session.
    setSessionRefresher(() => Promise.resolve('success'));
    const { fetch } = unauthorizedFetch();
    const replays = captureReplays();

    const result = await relayClient.GET('/api/health', {
      fetch,
      baseUrl: testBaseUrl,
    });

    expect(teardowns).toBe(1);
    expect(replays).toHaveLength(0);
    expect(result.response.status).toBe(401);
  });

  async function expectSignOutOn(outcome: 'terminal' | 'transient') {
    setSessionRefresher(() => Promise.resolve(outcome));
    const { fetch } = unauthorizedFetch();
    const replays = captureReplays();

    const result = await relayClient.GET('/api/health', {
      fetch,
      baseUrl: testBaseUrl,
    });

    expect(teardowns).toBe(1);
    expect(replays).toHaveLength(0);
    expect(result.response.status).toBe(401);
  }

  it('signs out when the refresh token is dead', async () => {
    await expectSignOutOn('terminal');
  });

  it('signs out when the refresh could not be completed', async () => {
    // The token in hand was already rejected, so there is nothing left to stay
    // signed in with even though the failure itself may be temporary.
    await expectSignOutOn('transient');
  });

  it('leaves a 401 from an unauthenticated path alone', async () => {
    // A mistyped password must not sign the user out of the session they are
    // still trying to establish, and there is no session to refresh yet.
    let refreshes = 0;
    let sessionRefreshes = 0;
    setUserTokenRefresher(() => {
      refreshes += 1;
      return Promise.resolve();
    });
    setSessionRefresher(() => {
      sessionRefreshes += 1;
      return Promise.resolve('success');
    });
    const { fetch } = unauthorizedFetch();
    const replays = captureReplays();

    const result = await relayClient.POST('/api/auth/login', {
      body: { email: 'user@example.com', password: 'wrong', refreshable: true },
      fetch,
      baseUrl: testBaseUrl,
    });

    expect(result.response.status).toBe(401);
    expect(refreshes).toBe(0);
    expect(sessionRefreshes).toBe(0);
    expect(teardowns).toBe(0);
    expect(replays).toHaveLength(0);
  });
});

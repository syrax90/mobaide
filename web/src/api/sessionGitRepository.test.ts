import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ApiError } from './errors';

// Hand-written stand-in for the typed agent client, as in
// `templateRepository.test.ts`: the proxy client is built with a relative base
// URL the undici Request under vitest refuses to parse, and what is under test
// is the repository's own request shape and error mapping.
const agent = vi.hoisted(() => ({
  results: [] as { data?: unknown; error?: unknown; response: Response }[],
  requests: [] as { agentId: string; path: string; init: unknown }[],
}));

vi.mock('./clients', () => ({
  createAgentClient: (agentId: string) => ({
    GET: (path: string, init: unknown) => {
      agent.requests.push({ agentId, path, init });
      const next = agent.results.shift();
      if (next === undefined) return Promise.reject(new Error(`unexpected GET ${path}`));
      return Promise.resolve(next);
    },
  }),
}));

const {
  SessionGitError,
  getSessionGitDiff,
  getSessionGitStatus,
  isSessionGitError,
  isTerminalSessionGitError,
  toSessionGitError,
} = await import('./sessionGitRepository');
const { toApiError } = await import('./errors');

afterEach(() => {
  agent.results.length = 0;
  agent.requests.length = 0;
});

/** Off the wire, as `toApiError` builds them from an openapi-fetch failure. */
const apiError = (status: number, body: unknown): ApiError =>
  toApiError(new Response(null, { status }), body);

/** What the last GET carried, typed loosely: the shape is the assertion. */
const lastParams = (): { path?: unknown; query?: unknown } =>
  (agent.requests.at(-1)?.init as { params: { path?: unknown; query?: unknown } }).params;

describe('toSessionGitError', () => {
  it('reads a session lookup miss off the 404 string FastAPI sends', () => {
    const cause = apiError(404, { detail: "Session 'abc-123' not found" });

    const error = toSessionGitError(cause);

    expect(error).toBeInstanceOf(SessionGitError);
    expect(error).toMatchObject({
      kind: 'sessionGone',
      code: null,
      detailMessage: null,
      cause,
      message: cause.message,
    });
  });

  it.each([
    ['the relay has no such proxy route', { detail: 'Unknown proxy route' }],
    ['the agent answers a plain 404', { detail: 'Not Found' }],
    ['the 404 carries no string at all', { detail: { error: 'missing' } }],
    ['the body is empty', undefined],
  ])('treats any other 404 as an agent that predates the routes when %s', (_case, body) => {
    const error = toSessionGitError(apiError(404, body));

    expect(error).toBeInstanceOf(SessionGitError);
    expect(error).toMatchObject({ kind: 'agentOutdated', code: null });
  });

  it('does not let a session id that mentions the phrase forge the other 404', () => {
    // Anchored on both ends: the relay's own detail strings never take this
    // exact shape, and a looser match would let a lookup miss read as outdated.
    expect(toSessionGitError(apiError(404, { detail: "Session 'x' not found today" }))).toMatchObject({
      kind: 'agentOutdated',
    });
  });

  it('types a coded git failure with its message', () => {
    const error = toSessionGitError(
      apiError(500, { detail: { code: 'git_error', message: 'fatal: bad object' } }),
    );

    expect(error).toMatchObject({
      kind: 'git',
      code: 'git_error',
      detailMessage: 'fatal: bad object',
    });
  });

  it('types a coded git failure without a message', () => {
    const error = toSessionGitError(apiError(503, { detail: { code: 'git_unavailable' } }));

    expect(error).toMatchObject({ kind: 'git', code: 'git_unavailable', detailMessage: null });
  });

  it('passes an ApiError with nothing it understands through unchanged', () => {
    // A 422 is `{"detail": [{loc, msg, type}]}`; a relay string on a 5xx has
    // no code. Neither is a git answer, so the caller keeps the original.
    const validation = apiError(422, { detail: [{ loc: ['query', 'path'], msg: 'required' }] });
    const relay = apiError(503, { detail: 'Agent is offline' });
    const coded = apiError(500, { detail: { error: 'internal' } });

    expect(toSessionGitError(validation)).toBe(validation);
    expect(toSessionGitError(relay)).toBe(relay);
    expect(toSessionGitError(coded)).toBe(coded);
  });

  it('passes a non-API failure through by reference', () => {
    const network = new TypeError('Failed to fetch');

    expect(toSessionGitError(network)).toBe(network);
    expect(toSessionGitError(undefined)).toBeUndefined();
  });
});

describe('isTerminalSessionGitError', () => {
  it('is true only for the two 404s, which no retry can change', () => {
    expect(
      isTerminalSessionGitError(toSessionGitError(apiError(404, { detail: "Session 's1' not found" }))),
    ).toBe(true);
    expect(isTerminalSessionGitError(toSessionGitError(apiError(404, { detail: 'Not Found' })))).toBe(
      true,
    );
    expect(
      isTerminalSessionGitError(toSessionGitError(apiError(500, { detail: { code: 'git_timeout' } }))),
    ).toBe(false);
    expect(isTerminalSessionGitError(apiError(404, { detail: 'Not Found' }))).toBe(false);
    expect(isTerminalSessionGitError(null)).toBe(false);
  });

  it('recognises only the wrapped class', () => {
    expect(isSessionGitError(new SessionGitError('git', apiError(500, {}), 'git_error'))).toBe(true);
    expect(isSessionGitError(apiError(500, {}))).toBe(false);
  });
});

describe('getSessionGitStatus', () => {
  const body = { session_id: 's1', etag: 'e1', unchanged: false, truncated: false, entry_limit: 5000 };

  it('asks the proxy of the given agent for the session, with no etag on a cold read', async () => {
    agent.results.push({ data: body, response: new Response(null, { status: 200 }) });

    await expect(getSessionGitStatus('a1', 's1')).resolves.toEqual(body);

    expect(agent.requests.at(-1)).toMatchObject({
      agentId: 'a1',
      path: '/api/sessions/{session_id}/git/status',
    });
    expect(lastParams()).toEqual({ path: { session_id: 's1' } });
  });

  it('carries the last etag as the query so an idle repo answers unchanged', async () => {
    agent.results.push({ data: { ...body, unchanged: true }, response: new Response(null, { status: 200 }) });

    await getSessionGitStatus('a1', 's1', 'e1');

    expect(lastParams()).toEqual({ path: { session_id: 's1' }, query: { etag: 'e1' } });
  });

  it('surfaces a wire failure already typed for the viewer', async () => {
    agent.results.push({
      error: { detail: "Session 's1' not found" },
      response: new Response(null, { status: 404 }),
    });

    await expect(getSessionGitStatus('a1', 's1')).rejects.toMatchObject({
      name: 'SessionGitError',
      kind: 'sessionGone',
    });
  });
});

describe('getSessionGitDiff', () => {
  const body = { path: 'src/app.py', scope: 'unstaged', kind: 'text', truncated: false };

  it('sends the context as the string FastAPI declares and omits orig_path for a plain change', async () => {
    agent.results.push({ data: body, response: new Response(null, { status: 200 }) });

    await expect(
      getSessionGitDiff('a1', 's1', { path: 'src/app.py', scope: 'unstaged', context: 10 }),
    ).resolves.toEqual(body);

    expect(agent.requests.at(-1)?.path).toBe('/api/sessions/{session_id}/git/diff');
    expect(lastParams()).toEqual({
      path: { session_id: 's1' },
      query: { path: 'src/app.py', scope: 'unstaged', context: '10' },
    });
  });

  it("names the rename's source so a pathspec-limited diff can still see it", async () => {
    agent.results.push({ data: body, response: new Response(null, { status: 200 }) });

    await getSessionGitDiff('a1', 's1', {
      path: 'src/new.py',
      scope: 'staged',
      context: 3,
      origPath: 'src/old.py',
    });

    expect(lastParams()).toEqual({
      path: { session_id: 's1' },
      query: { path: 'src/new.py', scope: 'staged', context: '3', orig_path: 'src/old.py' },
    });
  });

  it('surfaces a coded git failure typed for the viewer', async () => {
    agent.results.push({
      error: { detail: { code: 'git_timeout' } },
      response: new Response(null, { status: 504 }),
    });

    await expect(
      getSessionGitDiff('a1', 's1', { path: 'src/app.py', scope: 'unstaged', context: 3 }),
    ).rejects.toMatchObject({ name: 'SessionGitError', kind: 'git', code: 'git_timeout' });
  });
});

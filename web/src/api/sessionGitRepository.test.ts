import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ApiError } from './errors';

// Hand-written stand-in for the typed agent client, as in
// `templateRepository.test.ts`: the proxy client is built with a relative base
// URL the undici Request under vitest refuses to parse, and what is under test
// is the repository's own request shape and error mapping.
const agent = vi.hoisted(() => ({
  results: [] as { data?: unknown; error?: unknown; response: Response }[],
  requests: [] as { agentId: string; method: 'GET' | 'POST'; path: string; init: unknown }[],
}));

vi.mock('./clients', () => ({
  createAgentClient: (agentId: string) => {
    const call = (method: 'GET' | 'POST') => (path: string, init: unknown) => {
      agent.requests.push({ agentId, method, path, init });
      const next = agent.results.shift();
      if (next === undefined) return Promise.reject(new Error(`unexpected ${method} ${path}`));
      return Promise.resolve(next);
    };
    return { GET: call('GET'), POST: call('POST') };
  },
}));

const {
  SessionGitError,
  commitSessionGit,
  discardSessionGit,
  getSessionGitCommitContext,
  getSessionGitDiff,
  getSessionGitStatus,
  isSessionGitError,
  isTerminalSessionGitError,
  stageSessionGit,
  toSessionGitError,
  unstageSessionGit,
} = await import('./sessionGitRepository');
const { toApiError } = await import('./errors');

afterEach(() => {
  agent.results.length = 0;
  agent.requests.length = 0;
});

/** Off the wire, as `toApiError` builds them from an openapi-fetch failure. */
const apiError = (status: number, body: unknown): ApiError =>
  toApiError(new Response(null, { status }), body);

/** What the last request carried, typed loosely: the shape is the assertion. */
const lastParams = (): { path?: unknown; query?: unknown } =>
  (agent.requests.at(-1)?.init as { params: { path?: unknown; query?: unknown } }).params;

const lastBody = (): unknown => (agent.requests.at(-1)?.init as { body?: unknown }).body;

const ok = (data: unknown): void => {
  agent.results.push({ data, response: new Response(null, { status: 200 }) });
};

const refuse = (status: number, detail: unknown): void => {
  agent.results.push({ error: { detail }, response: new Response(null, { status }) });
};

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

describe('stageSessionGit / unstageSessionGit', () => {
  it.each([
    ['stage', stageSessionGit],
    ['unstage', unstageSessionGit],
  ] as const)('POSTs the paths to %s and resolves to nothing', async (leaf, call) => {
    ok({ ok: true });

    await expect(call('a1', 's1', ['src/new.py', 'src/old.py'])).resolves.toBeUndefined();

    expect(agent.requests.at(-1)).toMatchObject({
      agentId: 'a1',
      method: 'POST',
      path: `/api/sessions/{session_id}/git/${leaf}`,
    });
    expect(lastParams()).toEqual({ path: { session_id: 's1' } });
    expect(lastBody()).toEqual({ paths: ['src/new.py', 'src/old.py'] });
  });

  it('types a 409 refusal with its code and the git output', async () => {
    refuse(409, { code: 'pathspec_mismatch', message: "fatal: pathspec 'x' did not match" });

    await expect(stageSessionGit('a1', 's1', ['x'])).rejects.toMatchObject({
      name: 'SessionGitError',
      kind: 'git',
      code: 'pathspec_mismatch',
      detailMessage: "fatal: pathspec 'x' did not match",
    });
  });

  it('types the old-agent 404 so the panel can hide what it cannot do', async () => {
    refuse(404, 'Unknown proxy route');

    await expect(unstageSessionGit('a1', 's1', ['x'])).rejects.toMatchObject({
      kind: 'agentOutdated',
    });
  });
});

describe('discardSessionGit', () => {
  it('sends the paths with the scope git needs to pick restore or clean', async () => {
    ok({ ok: true });

    await expect(discardSessionGit('a1', 's1', ['notes.txt'], 'untracked')).resolves.toBeUndefined();

    expect(agent.requests.at(-1)).toMatchObject({
      method: 'POST',
      path: '/api/sessions/{session_id}/git/discard',
    });
    expect(lastParams()).toEqual({ path: { session_id: 's1' } });
    expect(lastBody()).toEqual({ paths: ['notes.txt'], scope: 'untracked' });
  });
});

describe('getSessionGitCommitContext', () => {
  it('reads the author and previous commit of the session', async () => {
    const body = { author: { name: 'Ada', email: 'ada@example.com' }, head: null };
    ok(body);

    await expect(getSessionGitCommitContext('a1', 's1')).resolves.toEqual(body);

    expect(agent.requests.at(-1)).toMatchObject({
      agentId: 'a1',
      method: 'GET',
      path: '/api/sessions/{session_id}/git/commit/context',
    });
    expect(lastParams()).toEqual({ path: { session_id: 's1' } });
  });

  it('surfaces a refusal typed for the sheet', async () => {
    refuse(409, { code: 'not_a_repo', message: '' });

    await expect(getSessionGitCommitContext('a1', 's1')).rejects.toMatchObject({
      kind: 'git',
      code: 'not_a_repo',
      detailMessage: '',
    });
  });
});

describe('commitSessionGit', () => {
  const head = {
    oid: '4890423520f4dcd158b85374f44fc8819a813ac2',
    short_oid: '4890423',
    subject: 'Fix the login redirect',
  };

  it('POSTs the request as is and resolves to the new head', async () => {
    ok(head);
    const request = { message: 'Fix the login redirect\n\nWhy.', amend: true, no_verify: false };

    await expect(commitSessionGit('a1', 's1', request)).resolves.toEqual(head);

    expect(agent.requests.at(-1)).toMatchObject({
      method: 'POST',
      path: '/api/sessions/{session_id}/git/commit',
    });
    expect(lastParams()).toEqual({ path: { session_id: 's1' } });
    expect(lastBody()).toEqual(request);
  });

  it('types a hook refusal with the hook output', async () => {
    refuse(409, { code: 'hook_failed', message: 'lint: nope' });

    await expect(
      commitSessionGit('a1', 's1', { message: 'x', amend: false, no_verify: false }),
    ).rejects.toMatchObject({ kind: 'git', code: 'hook_failed', detailMessage: 'lint: nope' });
  });

  it('types the blank-message 400 like any other coded failure', async () => {
    refuse(400, { code: 'empty_message' });

    await expect(
      commitSessionGit('a1', 's1', { message: ' ', amend: false, no_verify: false }),
    ).rejects.toMatchObject({ kind: 'git', code: 'empty_message', detailMessage: null });
  });
});

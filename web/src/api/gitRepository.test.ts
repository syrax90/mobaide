import { afterEach, describe, expect, it, vi } from 'vitest';
import type { GitItem } from './gitRepository';

// Hand-written stand-in for the typed agent client, as in
// `sessionGitRepository.test.ts`: what is under test is the repository's own
// request shape and the two-class error mapping of the branch routes.
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
  GitBusyError,
  canCheckOut,
  canRemoveWorktree,
  checkoutGitRef,
  compareRefNames,
  createGitBranch,
  isDeletable,
  listGitItems,
} = await import('./gitRepository');
const { SessionGitError } = await import('./sessionGitRepository');

afterEach(() => {
  agent.results.length = 0;
  agent.requests.length = 0;
});

const lastRequest = () => {
  const request = agent.requests.at(-1);
  if (request === undefined) throw new Error('no request was made');
  const init = request.init as { params: { path?: unknown; query?: unknown }; body?: unknown };
  return { method: request.method, path: request.path, params: init.params, body: init.body };
};

const ok = (data: unknown): void => {
  agent.results.push({ data, response: new Response(null, { status: 200 }) });
};

const refuse = (status: number, detail: unknown): void => {
  agent.results.push({ error: { detail }, response: new Response(null, { status }) });
};

const item = (over: Partial<GitItem> = {}): GitItem => ({
  kind: 'branch_only',
  branch: 'feature-a',
  worktree_path: null,
  ahead: 0,
  behind: 0,
  detached: false,
  gone: false,
  is_current: false,
  locked: false,
  locked_reason: null,
  prunable: false,
  upstream: null,
  remote: null,
  subject: null,
  committed_at: null,
  ...over,
});

const result = { branch: 'feature-a', checked_out: true, created: false };

describe('listGitItems', () => {
  it('always asks for the remote-tracking rows', async () => {
    const rows = [item(), item({ kind: 'remote_branch', branch: 'origin/main', remote: 'origin' })];
    ok({ items: rows });

    await expect(listGitItems('a1', 'proj')).resolves.toEqual(rows);

    expect(lastRequest()).toMatchObject({
      method: 'GET',
      path: '/api/projects/{name}/git/items',
      params: { path: { name: 'proj' }, query: { remote: true } },
    });
  });
});

describe('createGitBranch', () => {
  it('posts the request as given', async () => {
    ok({ branch: 'feature-a', checked_out: false, created: true });

    await expect(
      createGitBranch('a1', 'proj', {
        name: 'feature-a',
        start_point: 'main',
        checkout: false,
        force: false,
      }),
    ).resolves.toEqual({ branch: 'feature-a', checked_out: false, created: true });

    expect(lastRequest()).toMatchObject({
      method: 'POST',
      path: '/api/projects/{name}/git/branches',
      params: { path: { name: 'proj' } },
      body: { name: 'feature-a', start_point: 'main', checkout: false, force: false },
    });
  });

  it('types a coded refusal like the session routes', async () => {
    refuse(409, { code: 'exists', message: 'feature-a' });

    const error = await createGitBranch('a1', 'proj', {
      name: 'feature-a',
      checkout: false,
      force: false,
    }).catch((failure: unknown) => failure);

    expect(error).toBeInstanceOf(SessionGitError);
    expect(error).toMatchObject({ kind: 'git', code: 'exists', detailMessage: 'feature-a' });
  });
});

describe('checkoutGitRef', () => {
  it("posts the row's ref verbatim", async () => {
    ok(result);

    await expect(checkoutGitRef('a1', 'proj', 'origin/feature-a')).resolves.toEqual(result);

    expect(lastRequest()).toMatchObject({
      method: 'POST',
      path: '/api/projects/{name}/git/checkout',
      params: { path: { name: 'proj' } },
      body: { ref: 'origin/feature-a', force: false },
    });
  });

  it('keeps the busy 409 as the screen’s own class, holders attached', async () => {
    refuse(409, { busy_by: [{ kind: 'session', session_id: 's1', cwd: '/p' }] });

    const error = await checkoutGitRef('a1', 'proj', 'x').catch((failure: unknown) => failure);

    expect(error).toBeInstanceOf(GitBusyError);
    expect((error as InstanceType<typeof GitBusyError>).holders).toEqual([
      { kind: 'session', session_id: 's1', cwd: '/p' },
    ]);
  });

  it('types a coded 409 as a git refusal, not a busy conflict', async () => {
    refuse(409, { code: 'dirty', message: 'error: Your local changes' });

    const error = await checkoutGitRef('a1', 'proj', 'x').catch((failure: unknown) => failure);

    expect(error).not.toBeInstanceOf(GitBusyError);
    expect(error).toMatchObject({
      kind: 'git',
      code: 'dirty',
      detailMessage: 'error: Your local changes',
    });
  });

  it('reads a coded 404 as a missing ref and a plain one as an old agent', async () => {
    refuse(404, { code: 'ref_not_found' });
    refuse(404, 'Not Found');

    const missing = await checkoutGitRef('a1', 'proj', 'x').catch((failure: unknown) => failure);
    const outdated = await checkoutGitRef('a1', 'proj', 'x').catch((failure: unknown) => failure);

    expect(missing).toMatchObject({ kind: 'git', code: 'ref_not_found' });
    expect(outdated).toMatchObject({ kind: 'agentOutdated', code: null });
  });
});

describe('row eligibility', () => {
  it('offers delete to every local row but the current branch, never to a remote one without delete_remote', () => {
    expect(isDeletable(item(), false)).toBe(true);
    expect(isDeletable(item({ kind: 'branch_with_worktree', worktree_path: '/wt' }), false)).toBe(
      true,
    );
    expect(isDeletable(item({ is_current: true }), false)).toBe(false);
    expect(
      isDeletable(item({ kind: 'remote_branch', branch: 'origin/x', remote: 'origin' }), false),
    ).toBe(false);
  });

  it('offers check out only where nothing has the branch checked out', () => {
    expect(canCheckOut(item())).toBe(true);
    expect(canCheckOut(item({ kind: 'remote_branch', branch: 'origin/x', remote: 'origin' }))).toBe(
      true,
    );
    expect(canCheckOut(item({ is_current: true }))).toBe(false);
    expect(canCheckOut(item({ kind: 'branch_with_worktree', worktree_path: '/wt' }))).toBe(false);
    expect(
      canCheckOut(item({ kind: 'orphan_worktree', branch: null, worktree_path: '/wt' })),
    ).toBe(false);
  });
});

describe('remote delete and worktree removal eligibility', () => {
  const remote = item({ kind: 'remote_branch', branch: 'origin/x', remote: 'origin' });

  it('offers delete on a remote-tracking row only behind delete_remote', () => {
    expect(isDeletable(remote, true)).toBe(true);
    expect(isDeletable(remote, false)).toBe(false);
    expect(isDeletable(item(), false)).toBe(true);
    expect(isDeletable(item({ is_current: true }), true)).toBe(false);
  });

  it('offers Remove worktree only on a worktree row that is not the project checkout', () => {
    expect(canRemoveWorktree(item({ kind: 'branch_with_worktree', worktree_path: '/wt' }))).toBe(true);
    expect(
      canRemoveWorktree(
        item({ kind: 'branch_with_worktree', worktree_path: '/repo', is_current: true }),
      ),
    ).toBe(false);
    expect(canRemoveWorktree(item({ kind: 'branch_with_worktree', worktree_path: '' }))).toBe(false);
    expect(canRemoveWorktree(item({ kind: 'branch_with_worktree', worktree_path: null }))).toBe(false);
    expect(canRemoveWorktree(item())).toBe(false);
    expect(
      canRemoveWorktree(item({ kind: 'orphan_worktree', branch: null, worktree_path: '/wt' })),
    ).toBe(false);
    expect(canRemoveWorktree(remote)).toBe(false);
  });
});

describe('compareRefNames', () => {
  it('sorts case-insensitively, the raw name breaking a tie', () => {
    const names = ['main', 'Feature/x', 'feature/a', 'develop', 'fix/b', 'alpha', 'b', 'B'];

    expect([...names].sort(compareRefNames)).toEqual([
      'alpha',
      'B',
      'b',
      'develop',
      'feature/a',
      'Feature/x',
      'fix/b',
      'main',
    ]);
    expect(compareRefNames('main', 'main')).toBe(0);
  });

  it('compares UTF-16 code units, as Kotlin and Swift do, not code points or locale order', () => {
    // U+1F600 is above U+FF5E as a code point, but its lead surrogate 0xD83D is below it.
    expect(compareRefNames('\u{1F600}', '\uFF5E')).toBeLessThan(0);
    // Punctuation and digits keep their code-unit place before letters.
    expect(['b', '_x', '1'].sort(compareRefNames)).toEqual(['1', '_x', 'b']);
  });

  it('keeps two spellings of the same marks in code-unit order of their lowercased names', () => {
    // Canonically equivalent, but distinct refs to git: no normalisation may merge them.
    expect(compareRefNames('ą́', 'Ą́')).toBeLessThan(0);
    expect(compareRefNames('Ą́', 'ą́')).toBeGreaterThan(0);
  });
});

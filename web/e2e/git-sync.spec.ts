import { expect, test, type Locator, type Page, type WebSocketRoute } from '@playwright/test';
import { json, signIn, stubRelay } from './fixtures';

const AGENT_ID = 'agent-00000001';

/** `SessionResponse` with a cwd, which is what gates the terminal's ⋯ menu. */
const SESSION_ROW = {
  session_id: 's1',
  state: 'running',
  created_at: '2026-09-01T10:00:00+00:00',
  pid: 4242,
  command: ['claude'],
  cwd: '/srv/p1',
  title: 'claude',
  project_name: 'p1',
  kind: 'claude',
  kind_label: null,
  attention: null,
  exit_reason: null,
};

/** One modified, unstaged file: its Stage button is what a running job locks. */
const MODIFIED_ENTRY = {
  group: 'unstaged',
  path: 'src/app.py',
  orig_path: null,
  code: 'M',
  index_status: '.',
  worktree_status: 'M',
  rename_score: null,
  submodule: false,
};

/** `GitStatusResponse` on `main`, one commit ahead of `origin/main`. */
const STATUS = {
  session_id: 's1',
  cwd: '/srv/p1',
  etag: '3f0d4c1e9a7b2c5d6e8f9a0b1c2d3e4f5a6b7c8d',
  unchanged: false,
  unavailable_reason: null,
  repo: {
    toplevel: '/srv/p1',
    git_dir: '/srv/p1/.git',
    common_dir: '/srv/p1/.git',
    head: { oid: 'abc1234def', short_oid: 'abc1234', branch: 'main', detached: false, initial: false },
    upstream: { ref: 'origin/main', ahead: 1, behind: 0 },
    state: 'clean',
    is_linked_worktree: false,
  },
  entries: [MODIFIED_ENTRY],
  counts: { conflicts: 0, staged: 0, unstaged: 1, untracked: 0, total: 1 },
  truncated: false,
  entry_limit: 5000,
};

/** The agent's `GET /api/version` advertising every git mutation and job. */
const AGENT_VERSION = {
  name: 'mobaide-agent',
  server_version: '1.4.0',
  protocol_versions: [7],
  min_client_version: 1,
  capabilities: {
    git: { changes: true, stage: true, discard: true, commit: true, fetch: true, pull: true, push: true },
  },
  latest_agent_version: null,
};

const JOB_ID = '3f9c2b7e1d4a4c0f9b8e7d6c5a4b3c2d';

/** `GitJobResponse`, shaped like the `running_start` golden case. */
function job(over: Record<string, unknown> = {}) {
  return {
    job_id: JOB_ID,
    session_id: 's1',
    op: 'push',
    status: 'running',
    phase: null,
    percent: null,
    line: null,
    ok: null,
    error_code: null,
    message: null,
    ...over,
  };
}

interface Stub {
  /** The events socket, once the app opened it; frames go out through it. */
  events: () => WebSocketRoute;
  /** Every `POST …/git/push` body the app sent, in order. */
  pushes: unknown[];
}

/**
 * The relay, one running session with a cwd, the agent's read routes and the
 * job routes: a push answers `started`, the reconcile poll answers whatever
 * `snapshot` holds at the time.
 */
async function stubTerminal(
  page: Page,
  started: () => unknown,
  snapshot: () => unknown,
): Promise<Stub> {
  await stubRelay(page);
  let socket: WebSocketRoute | null = null;
  const pushes: unknown[] = [];
  await page.route('**/proxy/*/api/sessions', (route) => route.fulfill(json([SESSION_ROW])));
  // `stubRelay`'s `**/api/version` glob also matches the agent's probe, which
  // is where `capabilities.git` lives; registered later, this one wins for it.
  await page.route('**/proxy/*/api/version', (route) => route.fulfill(json(AGENT_VERSION)));
  await page.route(/\/proxy\/[^/]+\/api\/sessions\/[^/]+\/git\/status(\?.*)?$/, (route) =>
    route.fulfill(json(STATUS)),
  );
  await page.route(/\/proxy\/[^/]+\/api\/sessions\/[^/]+\/git\/push$/, (route) => {
    pushes.push(route.request().postDataJSON());
    return route.fulfill(json(started()));
  });
  await page.route(/\/proxy\/[^/]+\/api\/sessions\/[^/]+\/git\/jobs\/[^/]+$/, (route) =>
    route.fulfill(json(snapshot())),
  );
  await page.route('**/api/mobile/refresh', (route) =>
    route.fulfill({ ...json({ detail: 'no' }), status: 401 }),
  );
  await page.routeWebSocket(/\/relay\/ws\/events\//, (ws) => {
    socket = ws;
    ws.send(JSON.stringify({ type: 'sessions.changed', payload: { sessions: [SESSION_ROW] } }));
  });
  await page.routeWebSocket(/\/relay\/ws\/session\//, () => undefined);
  return {
    events: () => {
      if (socket === null) throw new Error('the events socket has not opened yet');
      return socket;
    },
    pushes,
  };
}

/** Sign in, land on the session and walk the ⋯ menu into the sync sheet. */
async function openSync(page: Page): Promise<{ panel: Locator; sheet: Locator }> {
  await page.goto('/login');
  await signIn(page);
  await expect(page).toHaveURL(/:\d+\/$/);
  await page.goto(`/agents/${AGENT_ID}/sessions/s1`);

  const more = page.getByRole('button', { name: 'More options', expanded: false });
  await expect(more).toBeVisible();
  await more.click();
  await expect(page.getByRole('menu', { name: 'More options' })).toBeVisible();
  await page.getByRole('menuitem', { name: 'Fetch / Pull / Push…', exact: true }).click();

  const panel = page.getByRole('dialog', { name: 'Git changes' });
  await expect(panel).toBeVisible();
  const sheet = page.getByRole('dialog', { name: 'Fetch / Pull / Push', exact: true });
  await expect(sheet).toBeVisible();
  return { panel, sheet };
}

function frame(type: string, payload: unknown): string {
  return JSON.stringify({ type, payload });
}

test('the ⋯ menu opens the sync sheet; a push streams its progress into the banner and clears itself when done', async ({
  page,
}) => {
  // The poll answers `running` until the socket says otherwise: the frames
  // are the fast path, the snapshot is what a poll in between would confirm.
  let latest: unknown = job();
  const stub = await stubTerminal(
    page,
    () => job(),
    () => latest,
  );

  const { panel, sheet } = await openSync(page);
  await expect(sheet.getByText('↑1 ↓0 · origin/main')).toBeVisible();
  await expect(sheet.getByRole('button', { name: /^Fetch/ })).toBeEnabled();
  await expect(sheet.getByRole('button', { name: /^Pull/ })).toBeDisabled();
  await expect(sheet.getByText('Nothing to pull')).toBeVisible();
  await expect(sheet.getByRole('button', { name: /^Push/ })).toBeEnabled();
  await expect(sheet.getByText('1 commit to origin/main')).toBeVisible();

  await sheet.getByRole('button', { name: /^Push/ }).click();

  await expect.poll(() => stub.pushes).toEqual([{ force: false }]);
  const banner = panel.locator('.git-panel-job');
  await expect(banner).toContainText('Pushing…');
  await expect(sheet.getByRole('status')).toContainText('Pushing…');
  await expect(sheet.getByRole('button', { name: 'Cancel' })).toBeEnabled();
  // The staged set is off limits while the job owns the repository.
  await expect(panel.getByRole('button', { name: 'Stage src/app.py' })).toBeDisabled();

  latest = job({ phase: 'Writing objects', percent: 45, line: 'Writing objects:  45% (14/32)' });
  stub.events().send(frame('git.job.progress', latest));

  await expect(banner.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '45');
  await expect(banner).toContainText('Writing objects:  45% (14/32)');

  // The sheet may go while the job runs; the banner carries on.
  await sheet.getByRole('button', { name: 'Close' }).click();
  await expect(sheet).toBeHidden();
  await expect(banner).toContainText('Pushing…');

  latest = job({
    status: 'done',
    phase: 'Writing objects',
    percent: 100,
    line: 'Writing objects: 100% (32/32), done.',
    ok: true,
    message: '443f30d..9419146 main -> main',
  });
  stub.events().send(frame('git.job.done', latest));
  stub.events().send(
    frame('git.changed', { project_name: 'p1', toplevel: '/srv/p1', common_dir: '/srv/p1/.git' }),
  );

  await expect(banner).toContainText('Pushed · 443f30d..9419146 main -> main');
  await expect(panel.getByRole('button', { name: 'Stage src/app.py' })).toBeEnabled();
  // A success clears its own banner after six seconds.
  await expect(banner).toBeHidden({ timeout: 8_000 });
});

test('a rejected push keeps its banner with Options…, and the confirmed follow-up pushes with the lease', async ({
  page,
}) => {
  const rejected = job({
    status: 'done',
    ok: false,
    error_code: 'rejected',
    message: '! [rejected] main -> main (fetch first)',
  });
  let started: unknown = job();
  let latest: unknown = rejected;
  const stub = await stubTerminal(
    page,
    () => started,
    () => latest,
  );

  const { panel, sheet } = await openSync(page);
  await sheet.getByRole('button', { name: /^Push/ }).click();

  // The reconcile poll fires at once and answers the outcome.
  const banner = panel.locator('.git-panel-job');
  await expect(banner).toContainText('Push failed: The remote rejected the push.');
  await expect(sheet.getByRole('alert')).toContainText('The remote rejected the push.');
  await expect(sheet.getByText('! [rejected] main -> main (fetch first)')).toBeVisible();
  await sheet.getByRole('button', { name: 'Close' }).click();
  await expect(sheet).toBeHidden();
  await expect(banner).toContainText('Push failed');
  await expect(panel.getByRole('button', { name: 'Stage src/app.py' })).toBeEnabled();

  await banner.getByRole('button', { name: 'Options…' }).click();
  await expect(sheet).toBeVisible();
  await sheet.getByRole('button', { name: 'Force push (with lease)…' }).click();

  const confirm = page.getByRole('alertdialog', { name: 'Force push?' });
  await expect(confirm).toBeVisible();
  await expect(confirm.getByRole('button', { name: 'Cancel' })).toBeFocused();
  await confirm.getByRole('button', { name: 'Cancel' }).click();
  await expect(confirm).toBeHidden();
  expect(stub.pushes).toEqual([{ force: false }]);

  started = job({ job_id: 'a1b2c3d4e5f60718293a4b5c6d7e8f90' });
  latest = job({ job_id: 'a1b2c3d4e5f60718293a4b5c6d7e8f90', phase: 'Writing objects', percent: 10 });
  await sheet.getByRole('button', { name: 'Force push (with lease)…' }).click();
  await page.getByRole('alertdialog', { name: 'Force push?' }).getByRole('button', { name: 'Force push' }).click();

  await expect.poll(() => stub.pushes).toEqual([{ force: false }, { force: true }]);
  await expect(banner).toContainText('Pushing…');
  await expect(banner.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '10');
});

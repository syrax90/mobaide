import { expect, test, type Locator, type Page } from '@playwright/test';
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

/** One modified, unstaged file: the row the tests stage. */
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

/** `GitStatusResponse`, shaped like the `linked_worktree_full` golden case. */
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

/** `STATUS` once the row was staged: the entry moved and the counts followed. */
const STAGED_STATUS = {
  ...STATUS,
  etag: '4a1e5d2f0b8c3d6e7f9a0b1c2d3e4f5a6b7c8d9e',
  entries: [{ ...MODIFIED_ENTRY, group: 'staged', index_status: 'M', worktree_status: '.' }],
  counts: { conflicts: 0, staged: 1, unstaged: 0, untracked: 0, total: 1 },
};

/** The agent's `GET /api/version` advertising every git mutation. */
const AGENT_VERSION = {
  name: 'mobaide-agent',
  server_version: '1.4.0',
  protocol_versions: [7],
  min_client_version: 1,
  capabilities: { git: { changes: true, stage: true, discard: true, commit: true } },
  latest_agent_version: null,
};

/** `GitDiffResponse`, shaped like the `text_modified` golden case. */
const DIFF = {
  path: 'src/app.py',
  orig_path: null,
  scope: 'unstaged',
  kind: 'text',
  change: 'modified',
  old_mode: '100644',
  new_mode: '100644',
  additions: 1,
  deletions: 1,
  hunks: [
    {
      old_start: 1,
      old_lines: 3,
      new_start: 1,
      new_lines: 4,
      heading: '',
      lines: [
        { t: 'ctx', old: 1, new: 1, text: 'import os', no_newline: false },
        { t: 'del', old: 2, new: null, text: 'x = 1', no_newline: false },
        { t: 'add', old: null, new: 2, text: 'x = 2', no_newline: false },
        { t: 'ctx', old: 3, new: 3, text: 'print(x)', no_newline: false },
        // Wider than any pane: the row must scroll, never clip.
        { t: 'add', old: null, new: 4, text: `y = '${'wide_'.repeat(60)}'`, no_newline: false },
      ],
    },
  ],
  truncated: false,
  total_lines: 5,
  line_limit: 5000,
  byte_limit: 1048576,
};

/**
 * The relay, one running session with a cwd, and the agent's read routes. The
 * status body is read per request, so a test can move the tree between polls.
 */
async function stubTerminal(page: Page, status: () => unknown): Promise<void> {
  await stubRelay(page);
  // Registered after `stubRelay`, so these win over its empty session list.
  // Regexes rather than globs: a glob `*` stops at `/`, and the status URL
  // carries `?etag=` from the second poll on.
  await page.route('**/proxy/*/api/sessions', (route) => route.fulfill(json([SESSION_ROW])));
  await page.route(/\/proxy\/[^/]+\/api\/sessions\/[^/]+\/git\/status(\?.*)?$/, (route) =>
    route.fulfill(json(status())),
  );
  await page.route(/\/proxy\/[^/]+\/api\/sessions\/[^/]+\/git\/diff\?/, (route) =>
    route.fulfill(json(DIFF)),
  );
  // The stubbed agent token is not a JWT, so every reuse tries a rotation
  // first; refusing it keeps that off the log and falls back to the mint.
  await page.route('**/api/mobile/refresh', (route) =>
    route.fulfill({ ...json({ detail: 'no' }), status: 401 }),
  );
  // The session row is gated on a synced snapshot from the events socket —
  // without one the header never learns the cwd, and a snapshot missing `s1`
  // would eject the terminal as a dead session.
  await page.routeWebSocket(/\/relay\/ws\/events\//, (ws) => {
    ws.send(JSON.stringify({ type: 'sessions.changed', payload: { sessions: [SESSION_ROW] } }));
  });
  // A mocked PTY socket that never answers: the header renders regardless,
  // and it keeps the reconnect loop off the log.
  await page.routeWebSocket(/\/relay\/ws\/session\//, () => undefined);
}

/** Sign in, land on the session and walk the ⋯ menu into the viewer. */
async function openChanges(page: Page): Promise<Locator> {
  await page.goto('/login');
  await signIn(page);
  await expect(page).toHaveURL(/:\d+\/$/);
  await page.goto(`/agents/${AGENT_ID}/sessions/s1`);

  const more = page.getByRole('button', { name: 'More options', expanded: false });
  await expect(more).toBeVisible();
  await more.click();
  await expect(page.getByRole('menu', { name: 'More options' })).toBeVisible();
  await expect(page.getByRole('menuitem', { name: 'Changes…', exact: true })).toBeFocused();
  await page.getByRole('menuitem', { name: 'Changes…', exact: true }).click();

  const panel = page.getByRole('dialog', { name: 'Git changes' });
  await expect(panel).toBeVisible();
  return panel;
}

test('the ⋯ menu opens the Changes viewer beside a live terminal', async ({ page }) => {
  await stubTerminal(page, () => STATUS);

  const panel = await openChanges(page);
  await expect(panel.getByRole('button', { name: 'Close' })).toBeFocused();
  await expect(panel.getByText('main', { exact: true })).toBeVisible();
  await expect(panel.getByText('↑1 ↓0 · origin/main')).toBeVisible();

  await panel.getByRole('button', { name: /app\.py/ }).click();
  await expect(panel.getByRole('region', { name: 'Changes to src/app.py' })).toBeVisible();
  await expect(panel.getByText('x = 2', { exact: true })).toBeVisible();
  await expect(panel.getByRole('button', { name: 'Changes', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await expect(panel.getByRole('button', { name: 'Full file', exact: true })).toHaveAttribute(
    'aria-pressed',
    'false',
  );
  // Paint containment on the hunks must not swallow the horizontal scroll.
  const body = panel.getByRole('region', { name: 'Changes to src/app.py' });
  // Typed structurally: the e2e tsconfig carries no DOM lib.
  await expect
    .poll(() =>
      body.evaluate(
        (node: { scrollWidth: number; clientWidth: number }) => node.scrollWidth > node.clientWidth,
      ),
    )
    .toBe(true);

  // Non-modal: the terminal's own chrome stays reachable underneath.
  await expect(page.getByRole('link', { name: '← Sessions' })).toBeVisible();

  await page.keyboard.press('Escape');
  await expect(panel).toBeHidden();
  await expect(page.getByRole('button', { name: 'More options' })).toBeFocused();
});

test('stages a row and commits the staged set, reporting the new head inline', async ({
  page,
}) => {
  // The tree moves with the stage: the status poll that follows is what puts
  // the row under Staged and enables Commit…, exactly as the agent answers.
  let staged = false;
  await stubTerminal(page, () => (staged ? STAGED_STATUS : STATUS));
  // `stubRelay`'s `**/api/version` glob also matches the agent's probe, which
  // is where `capabilities.git` lives; registered later, this one wins for it.
  await page.route('**/proxy/*/api/version', (route) => route.fulfill(json(AGENT_VERSION)));
  await page.route(/\/proxy\/[^/]+\/api\/sessions\/[^/]+\/git\/stage$/, (route) => {
    staged = true;
    return route.fulfill(json({ ok: true }));
  });
  await page.route(/\/proxy\/[^/]+\/api\/sessions\/[^/]+\/git\/commit\/context$/, (route) =>
    route.fulfill(json({ author: { name: 'Ada', email: 'ada@example.com' }, head: null })),
  );
  await page.route(/\/proxy\/[^/]+\/api\/sessions\/[^/]+\/git\/commit$/, (route) =>
    route.fulfill(
      json({
        oid: 'abc1234abc1234abc1234abc1234abc1234abc12',
        short_oid: 'abc1234',
        subject: 'Smoke',
      }),
    ),
  );

  const panel = await openChanges(page);
  await expect(panel.getByRole('heading', { level: 3, name: /^Changes/ })).toBeVisible();
  // Nothing staged yet: the button is there (the agent can commit) but inert.
  await expect(panel.getByRole('button', { name: 'Commit…' })).toBeDisabled();

  await panel.getByRole('button', { name: 'Stage src/app.py' }).click();

  await expect(panel.getByRole('heading', { level: 3, name: /^Staged/ })).toBeVisible();
  await expect(panel.getByRole('heading', { level: 3, name: /^Changes/ })).toBeHidden();

  await panel.getByRole('button', { name: 'Commit…' }).click();
  const sheet = page.getByRole('dialog', { name: 'Commit', exact: true });
  await expect(sheet.getByText('Committing as Ada <ada@example.com>')).toBeVisible();
  await sheet.getByLabel('Summary').fill('Smoke');
  await sheet.getByRole('button', { name: 'Commit 1 file' }).click();

  // Inline, not a toast: the panel sits on the top layer, above the toasts.
  await expect(sheet).toBeHidden();
  await expect(panel.getByRole('status')).toContainText('Committed abc1234 · Smoke');
});

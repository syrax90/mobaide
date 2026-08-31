import { expect, test } from '@playwright/test';
import { PENDING_AGENT, signIn, stubRelay } from './fixtures';

const FP = 'f'.repeat(64);

function payload(expUnix = Math.floor(Date.now() / 1000) + 3600): string {
  return `MOBA1|https://relay.test|nonce-e2e|${FP}|${String(expUnix)}`;
}

/** What `pairing_qr.format_pairing_link` prints: base64url, padding stripped. */
function fragmentFor(text: string): string {
  return Buffer.from(text, 'utf8').toString('base64url');
}

test('a pairing link opened while signed out survives login and claims', async ({
  page,
}) => {
  const relay = await stubRelay(page, undefined, { pendingPairing: true });

  // The nonce lives in the fragment, which never survives a guard redirect —
  // `/pair` is public precisely so it can read and stash it first.
  await page.goto(`/pair#${fragmentFor(payload())}`);
  await expect(page).toHaveURL(/\/login\?next=%2Fpair$/);

  await signIn(page);

  await expect(page).toHaveURL(/\/pair$/);
  await expect(page.getByRole('heading', { name: 'Pair this agent?' })).toBeVisible();
  await expect(page.getByText('https://relay.test')).toBeVisible();

  await page.getByRole('button', { name: 'Pair' }).click();

  await expect(page.getByRole('heading', { name: /Waiting for/ })).toBeVisible();
  relay.bringPendingAgentOnline();

  await expect(
    page.getByRole('heading', { name: new RegExp(`${PENDING_AGENT.name}.*online`) }),
  ).toBeVisible({ timeout: 30_000 });
});

test('the pairing nonce never stays in the address bar or in history', async ({
  page,
}) => {
  await stubRelay(page, undefined, { pendingPairing: true });

  await page.goto(`/pair#${fragmentFor(payload())}`);
  // Already scrubbed on the way to sign-in: the bounce must not carry the
  // nonce either, which is why the guard's `?next=` is a bare `/pair`.
  await expect(page).toHaveURL(/\/login\?next=%2Fpair$/);
  await signIn(page);

  await expect(page.getByRole('heading', { name: 'Pair this agent?' })).toBeVisible();
  // `navigate(replace)`, not `location.hash = ''`: the latter pushes a history
  // entry that Back would re-expose the nonce from.
  await expect(page).toHaveURL(/\/pair$/);

  await page.goBack();

  expect(page.url()).not.toContain('nonce-e2e');
  expect(page.url()).not.toContain('#');
});

test('an expired pairing link says how stale it is instead of claiming', async ({
  page,
}) => {
  await stubRelay(page, undefined, { pendingPairing: true });
  const stale = payload(Math.floor(Date.now() / 1000) - 5 * 60);

  await page.goto(`/pair#${fragmentFor(stale)}`);
  await signIn(page);

  await expect(page.getByRole('alert')).toContainText('expired 5 minutes ago');
  await expect(page.getByRole('button', { name: 'Pair' })).toHaveCount(0);
});

test('a pasted payload feeds the same pipeline as a deep link', async ({ page }) => {
  await stubRelay(page, undefined, { pendingPairing: true });

  // No fragment: the day-one path, since the agent fleet only converges on a
  // printed link after the next release.
  await page.goto('/pair');
  await signIn(page);

  // Line breaks are what a soft-wrapped terminal copy carries.
  await page.getByLabel('Pairing code').fill(payload().replace('|nonce', '|non\nce'));
  await page.getByRole('button', { name: 'Continue' }).click();

  await expect(page.getByRole('heading', { name: 'Pair this agent?' })).toBeVisible();
});

test('the setup guide pairs a pasted payload without a detour through /pair', async ({
  page,
}) => {
  await stubRelay(page, undefined, { pendingPairing: true });

  await page.goto('/agents');
  await signIn(page);

  // The install command is on the page itself, not behind a button — it is the
  // only step a first-run user can act on.
  await expect(page.getByRole('heading', { name: 'No agents yet' })).toBeVisible();
  await expect(
    page.getByText('curl -fsSL https://get.mobaide.com/install.sh | bash'),
  ).toBeVisible();
  await expect(page.getByTestId('setup-example-output')).toContainText(
    '[mobaide-agent] Or open this link in a browser:',
  );

  await page.getByLabel('Pairing code or link').fill(payload());
  await page.getByRole('button', { name: 'Pair' }).click();

  await expect(page.getByRole('heading', { name: 'Pair this agent?' })).toBeVisible();
  // The handoff rides the fragment, so `/pair`'s scrub has to catch it here too.
  await expect(page).toHaveURL(/\/pair$/);
  expect(page.url()).not.toContain('nonce-e2e');
});

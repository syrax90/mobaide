import { expect, test } from '@playwright/test';
import { SERVER_INFO, THIS_DEVICE_ID, signIn, stubRelay } from './fixtures';

test('an unauthenticated visit is bounced to sign-in and returns to the deep link', async ({
  page,
}) => {
  await stubRelay(page);

  await page.goto('/devices');
  await expect(page).toHaveURL(/\/login\?next=%2Fdevices$/);
  await signIn(page);

  await expect(page).toHaveURL(/\/devices$/);
  await expect(page.getByRole('heading', { level: 1, name: 'My devices' })).toBeVisible();
});

test('the devices list labels every platform and flags this browser', async ({
  context,
  page,
}) => {
  // Source string rather than a callback: the e2e tsconfig has no DOM lib, so
  // a callback body cannot reference `window`.
  await context.addInitScript(
    `window.localStorage.setItem('mobaide-device-id', '${THIS_DEVICE_ID}');`,
  );
  await stubRelay(page);

  await page.goto('/devices');
  await signIn(page);

  const cards = page.getByRole('listitem');
  await expect(cards).toHaveCount(4);
  await expect(cards.filter({ hasText: 'Chrome on macOS' })).toContainText('Browser');
  await expect(cards.filter({ hasText: 'Personal iPhone' })).toContainText('iOS');
  await expect(cards.filter({ hasText: 'Pixel 8' })).toContainText('Android');
  // Registered before any client sent `platform`.
  await expect(cards.filter({ hasText: 'Old tablet' })).toContainText('Unknown');
  await expect(cards.filter({ hasText: 'Chrome on macOS' })).toContainText('This device');
  await expect(cards.filter({ hasText: 'Pixel 8' })).not.toContainText('This device');
});

test('revoking warns that every paired agent is signed out too', async ({ page }) => {
  await stubRelay(page);

  await page.goto('/devices');
  await signIn(page);
  await page
    .getByRole('listitem')
    .filter({ hasText: 'Pixel 8' })
    .getByRole('button', { name: 'Revoke' })
    .click();

  await expect(page.getByRole('alertdialog')).toContainText(
    'Every agent paired with it is signed out too.',
  );
});

test('a second tab inherits the session and follows the sign-out', async ({
  context,
}) => {
  await stubRelay(context);
  const first = await context.newPage();
  await first.goto('/login');
  await signIn(first);
  await expect(first).toHaveURL(/:\d+\/$/);

  // sessionStorage is per-tab, so this page starts with nothing and can only
  // reach /account by inheriting the session over the BroadcastChannel.
  const second = await context.newPage();
  await second.goto('/account');
  await expect(second.getByRole('heading', { level: 1, name: 'Account' })).toBeVisible();

  await first.goto('/account');
  await first.getByRole('button', { name: 'Sign out' }).click();

  await expect(second).toHaveURL(/\/login/);
  await expect(second.getByRole('button', { name: 'Sign In' })).toBeVisible();
});

test('an incompatible server takes the whole app over', async ({ page }) => {
  await stubRelay(page, {
    ...SERVER_INFO,
    protocol_versions: [9, 10],
    min_client_version: 9,
  });

  await page.goto('/login');

  await expect(
    page.getByRole('heading', { level: 1, name: 'Update required' }),
  ).toBeVisible();
  // A takeover, not a route: the sign-in form must be gone entirely.
  await expect(page.getByRole('button', { name: 'Sign In' })).toHaveCount(0);
});

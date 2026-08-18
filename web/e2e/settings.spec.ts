import { expect, test, type Page } from '@playwright/test';
import { signIn, stubRelay } from './fixtures';

/**
 * Replace `Notification` with a stub that counts `requestPermission()` calls,
 * installed before any application code runs.
 *
 * A unit test can only prove the Settings component does not call the service;
 * this proves nothing anywhere in the bundle raises the prompt on load, which is
 * what the acceptance criterion is about. Playwright's own permission handling
 * would auto-answer the prompt rather than record it, so the API is replaced
 * outright.
 *
 * Browser-side code goes in as a **string**, the shape `auth.spec.ts` uses: the
 * e2e tsconfig ships `lib: ["ES2023"]` with no DOM, so a callback referencing
 * `window` would not typecheck.
 */
async function stubNotifications(page: Page): Promise<() => Promise<number>> {
  await page.addInitScript(`
    window.__permissionRequests = 0;
    class StubNotification {
      static permission = 'default';
      static requestPermission() {
        window.__permissionRequests += 1;
        StubNotification.permission = 'granted';
        return Promise.resolve('granted');
      }
    }
    Object.defineProperty(window, 'Notification', {
      configurable: true,
      writable: true,
      value: StubNotification,
    });
  `);
  return () => page.evaluate<number>('window.__permissionRequests');
}

test('settings never prompts for notification permission on load', async ({ page }) => {
  const requests = await stubNotifications(page);
  await stubRelay(page);

  await page.goto('/login');
  await signIn(page);

  // Not on the sessions list the app lands on…
  expect(await requests()).toBe(0);

  await page
    .getByRole('navigation', { name: 'Main' })
    .getByRole('link', { name: 'Settings' })
    .click();
  await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible();

  // …and not on the screen that owns the button either.
  expect(await requests()).toBe(0);

  await page.getByRole('button', { name: 'Allow system notifications' }).click();
  await expect(page.getByText('System notifications are allowed.')).toBeVisible();
  expect(await requests()).toBe(1);
});

test('settings links onward to the surfaces mobile keeps behind it', async ({ page }) => {
  await stubRelay(page);

  await page.goto('/login');
  await signIn(page);
  // Wait for the sign-in to land before navigating away: `signIn` only clicks,
  // and a `goto` racing the login POST aborts it before the session is stored,
  // so the guard would bounce this straight back to /login.
  await expect(page).toHaveURL(/:\d+\/$/);
  await page.goto('/settings');

  await expect(page.getByRole('link', { name: 'My devices' })).toHaveAttribute('href', '/devices');
  await expect(page.getByRole('link', { name: 'Prompt templates' })).toHaveAttribute(
    'href',
    '/templates',
  );
  // Web ships Full display only, so there is no columns control to find.
  await expect(page.getByRole('spinbutton')).toHaveCount(0);
});

test('settings carries a theme picker that drives the header one', async ({ page }) => {
  await stubRelay(page);
  // Pinned so `system` resolves deterministically rather than inheriting the
  // machine's appearance.
  await page.emulateMedia({ colorScheme: 'light' });

  await page.goto('/login');
  await signIn(page);
  await expect(page).toHaveURL(/:\d+\/$/);
  await page.goto('/settings');

  // Both pickers are mounted here — the header's and the card's. They must be
  // two radio groups, not one: radios group by `name` document-wide, so a
  // shared literal name would make checking one uncheck the other.
  const pickers = page.getByRole('radiogroup', { name: 'Theme' });
  await expect(pickers).toHaveCount(2);

  // The radio is visually hidden behind its label, so the segment text is both
  // the real click target and the only one Playwright can act on.
  await pickers.last().getByText('Dark', { exact: true }).click();

  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  // `exact` keeps the substring match off Auto's "(currently dark)" name.
  await expect(
    pickers.first().getByRole('radio', { name: 'Dark', exact: true }),
  ).toBeChecked();
  await expect(page.getByText(/Auto follows your system appearance/)).toBeVisible();
});

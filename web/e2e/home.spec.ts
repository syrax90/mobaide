import { expect, test } from '@playwright/test';
import { signIn, stubRelay } from './fixtures';

test('home page renders the heading and nav brand for a signed-in visitor', async ({
  page,
}) => {
  await stubRelay(page);

  await page.goto('/login');
  await signIn(page);
  await expect(page).toHaveURL(/:\d+\/$/);

  await expect(
    page.getByRole('heading', { level: 1, name: 'Mobaide Web' }),
  ).toBeVisible();
  await expect(
    page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Mobaide' }),
  ).toBeVisible();
});

test('theme toggle cycles the preference and stamps html[data-theme]', async ({ page }) => {
  // The toggle lives in the shared ThemeToggle component rendered by both
  // AuthLayout and RootLayout, so /login (reachable while signed out) is
  // enough — no sign-in round trip needed for this assertion.
  await page.goto('/login');

  const html = page.locator('html');
  const toggle = page.getByRole('button', { name: /^Theme:/ });
  await expect(toggle).toHaveText('Theme: system');

  await toggle.click();
  await expect(toggle).toHaveText('Theme: light');
  await expect(html).toHaveAttribute('data-theme', 'light');

  await toggle.click();
  await expect(toggle).toHaveText('Theme: dark');
  await expect(html).toHaveAttribute('data-theme', 'dark');
});

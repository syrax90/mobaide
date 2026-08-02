import { expect, test } from '@playwright/test';

test('home page renders the heading and nav brand', async ({ page }) => {
  await page.goto('/');

  await expect(
    page.getByRole('heading', { level: 1, name: 'Mobaide Web' }),
  ).toBeVisible();
  await expect(
    page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Mobaide' }),
  ).toBeVisible();
});

test('theme toggle cycles the preference and stamps html[data-theme]', async ({ page }) => {
  await page.goto('/');

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

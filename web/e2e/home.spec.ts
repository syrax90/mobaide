import { expect, test } from '@playwright/test';
import { signIn, stubRelay } from './fixtures';

test('sessions list renders the heading and nav brand for a signed-in visitor', async ({
  page,
}) => {
  await stubRelay(page);

  await page.goto('/login');
  await signIn(page);
  await expect(page).toHaveURL(/:\d+\/$/);

  await expect(
    page.getByRole('heading', { level: 1, name: 'Sessions' }),
  ).toBeVisible();
  await expect(
    page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Mobaide' }),
  ).toBeVisible();
});

test('theme picker selects a preference and stamps html[data-theme]', async ({ page }) => {
  // The picker lives in the shared ThemePicker component rendered by both
  // AuthLayout and RootLayout, so /login (reachable while signed out) is
  // enough — no sign-in round trip needed for this assertion.
  //
  // The OS side is pinned to light so `system` resolves deterministically;
  // without it the run inherits the machine's appearance and the Auto
  // assertions flip with it.
  await page.emulateMedia({ colorScheme: 'light' });
  await page.goto('/login');

  const html = page.locator('html');
  const picker = page.getByRole('radiogroup', { name: 'Theme' });
  const auto = picker.getByRole('radio', { name: /^Auto/ });
  // `exact` is load-bearing: accessible-name matching is a substring match by
  // default, and a bare 'Light' also hits Auto's "(currently light)".
  const light = picker.getByRole('radio', { name: 'Light', exact: true });
  const dark = picker.getByRole('radio', { name: 'Dark', exact: true });
  // Each radio is visually hidden behind its label, so the segment text is both
  // the real click target and the only one Playwright can act on.
  const segment = (label: string) => picker.getByText(label, { exact: true });

  await expect(auto).toBeChecked();
  await expect(auto).toHaveAccessibleName('Auto — follows your system (currently light)');
  await expect(html).toHaveAttribute('data-theme', 'light');

  // The bug this control replaced: under a light OS, `system -> light` repaints
  // nothing, so the cycle button looked dead and took two clicks to respond.
  // data-theme is expected to hold still here — the selection moving is the
  // whole point.
  await segment('Light').click();
  await expect(light).toBeChecked();
  await expect(auto).not.toBeChecked();
  await expect(html).toHaveAttribute('data-theme', 'light');

  await segment('Dark').click();
  await expect(dark).toBeChecked();
  await expect(html).toHaveAttribute('data-theme', 'dark');

  await segment('Auto').click();
  await expect(auto).toBeChecked();
  await expect(html).toHaveAttribute('data-theme', 'light');
});

/**
 * Native radios were chosen over an ARIA button group precisely so arrow-key
 * selection arrives for free — but the inputs are visually hidden behind their
 * labels, and a hiding technique that took them out of the tab order (or a
 * later swap to `aria-checked` buttons) would strand a keyboard-only visitor
 * with no way to change theme at all. jsdom implements none of this, so the
 * assertion only exists in a real browser.
 */
test('theme picker selects with the arrow keys', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' });
  await page.goto('/login');

  const html = page.locator('html');
  const picker = page.getByRole('radiogroup', { name: 'Theme' });
  const auto = picker.getByRole('radio', { name: /^Auto/ });
  // `exact` is load-bearing here too: a bare 'Light' also hits Auto's
  // "(currently light)".
  const light = picker.getByRole('radio', { name: 'Light', exact: true });
  const dark = picker.getByRole('radio', { name: 'Dark', exact: true });

  await auto.focus();
  await expect(auto).toBeFocused();

  await page.keyboard.press('ArrowRight');
  await expect(light).toBeChecked();

  await page.keyboard.press('ArrowRight');
  await expect(dark).toBeChecked();
  // Focus rides along with the selection, which is what makes a second press
  // land on the next segment rather than repeating the first move.
  await expect(dark).toBeFocused();
  await expect(html).toHaveAttribute('data-theme', 'dark');
});

test('theme picker follows the OS appearance while Auto is selected', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' });
  await page.goto('/login');

  const html = page.locator('html');
  const auto = page.getByRole('radiogroup', { name: 'Theme' }).getByRole('radio', {
    name: /^Auto/,
  });
  await expect(auto).toBeChecked();

  await page.emulateMedia({ colorScheme: 'dark' });

  await expect(html).toHaveAttribute('data-theme', 'dark');
  await expect(auto).toHaveAccessibleName('Auto — follows your system (currently dark)');
});

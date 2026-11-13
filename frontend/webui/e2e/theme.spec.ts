import { test, expect } from '@playwright/test';
import { installMocks } from './fixtures/mock';

const shots = process.env.MUNIN_THEME_SHOTS;

test.describe('theme', () => {
  for (const scheme of ['light', 'dark'] as const) {
    test(`with no stored choice the system decides: ${scheme}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await installMocks(page);
      await page.goto('/');
      await expect(page.locator('html')).toHaveAttribute('data-theme', scheme);
      await expect(page.locator('html')).toHaveAttribute('data-theme-pref', 'system');
      await expect(page.locator('body')).toHaveCSS('background-color', scheme === 'dark' ? 'rgb(13, 18, 23)' : 'rgb(255, 255, 255)');
    });
  }

  test('the header toggle flips the theme, stores it, and names its next click', async ({ page, context }) => {
    await page.emulateMedia({ colorScheme: 'dark' });
    await installMocks(page);
    await page.goto('/');
    const toggle = page.getByTestId('theme-toggle');
    await expect(toggle).toHaveAccessibleName('Switch to light theme');
    await toggle.click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    await expect(toggle).toHaveAccessibleName('Switch to dark theme');
    expect((await context.cookies()).find(c => c.name === 'munin_theme')?.value).toBe('light');
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  });

  test('choosing Light in Settings applies at once, is stored, and survives a reload', async ({ page, context }) => {
    await installMocks(page);
    await page.goto('/');
    await page.getByText('e2e@test.local').click();
    await page.getByRole('menuitem', { name: 'Settings' }).click();
    await page.getByRole('radio', { name: 'Light' }).check({ force: true });

    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(255, 255, 255)');
    await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute('content', /^#f{3}(f{3})?$/i);
    const cookie = (await context.cookies()).find(c => c.name === 'munin_theme');
    expect(cookie?.value).toBe('light');

    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    if (shots) await page.screenshot({ path: `${shots}/settings-light.png`, fullPage: true });
  });

  test('the theme is set before the app script runs, so the page never flashes dark', async ({ page, context }) => {
    await context.addCookies([{ name: 'munin_theme', value: 'light', url: 'http://localhost:4173' }]);
    await installMocks(page);
    await page.addInitScript(() => {
      document.addEventListener('readystatechange', () => {
        if (document.readyState === 'interactive') {
          (window as unknown as { themeAtParse: string | null }).themeAtParse =
            document.documentElement.getAttribute('data-theme');
        }
      });
    });
    await page.goto('/');
    expect(await page.evaluate(() => (window as unknown as { themeAtParse: string | null }).themeAtParse)).toBe('light');
  });

  test('Match system follows the system, including a change while open', async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'dark' });
    await installMocks(page);
    await page.goto('/');
    await page.getByText('e2e@test.local').click();
    await page.getByRole('menuitem', { name: 'Settings' }).click();
    await page.getByRole('radio', { name: 'Match system' }).check({ force: true });
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.emulateMedia({ colorScheme: 'light' });
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  });

  test('screens in light, for a look', async ({ page, context }) => {
    test.skip(!shots, 'set MUNIN_THEME_SHOTS to a folder to save screenshots');
    await context.addCookies([{ name: 'munin_theme', value: 'light', url: 'http://localhost:4173' }]);
    await installMocks(page);
    await page.goto('/');
    await page.getByTestId('composer-textarea').fill('Hello');
    await page.getByTestId('composer-send').click();
    await page.waitForTimeout(800);
    await page.screenshot({ path: `${shots}/chat-light.png`, fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: `${shots}/chat-light-phone.png` });
  });
});

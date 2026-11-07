import { test, expect, type Page } from '@playwright/test';
import { measureContrast, formatFailures } from '../e2e/fixtures/contrast';

// Light mode on the real site, signed in. See playwright.live.config.ts: run the
// 'theme' project after signing in and before 'live', whose last test signs out.
// These only change the theme cookie of the test browser, never the account.
//
// MUNIN_THEME_SHOTS, if set, names a folder OUTSIDE the repo for screenshots. They
// show the account's chat list, so delete them after looking.

const RUN = 'playwright-live-check';
const shots = process.env.MUNIN_THEME_SHOTS;

type Theme = 'light' | 'dark';
const BG: Record<Theme, string> = { light: 'rgb(255, 255, 255)', dark: 'rgb(15, 20, 25)' };

function siteDomain(page: Page): string {
  return new URL(page.url()).hostname.split('.').slice(1).join('.');
}

async function shot(page: Page, name: string) {
  if (shots) await page.screenshot({ path: `${shots}/${name}.png`, fullPage: false });
}

async function expectTheme(page: Page, theme: Theme) {
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  await expect(page.locator('body')).toHaveCSS('background-color', BG[theme]);
}

async function openSettings(page: Page) {
  const me = await page.request.get(`https://auth.${siteDomain(page)}/auth/me`);
  expect(me.ok(), 'signed in').toBeTruthy();
  await page.getByText((await me.json()).email).first().click();
  await page.getByRole('menuitem', { name: 'Settings' }).click();
}

// Contrast on real content is reported, not enforced: the mocked suite enforces it.
async function reportContrast(page: Page, where: string) {
  const report = await measureContrast(page);
  test.info().annotations.push({
    type: `contrast ${where}`,
    description: report.failures.length
      ? `${report.failures.length} of ${report.checked} under the minimum:\n${formatFailures(report.failures)}`
      : `all ${report.checked} pass`,
  });
}

test('a choice in chat Settings reaches the static pages, and theirs reaches chat', async ({ page, context }) => {
  await page.goto('/');
  await openSettings(page);
  await page.getByRole('radio', { name: 'Light' }).check({ force: true });
  await expectTheme(page, 'light');

  const cookie = (await context.cookies()).find((c) => c.name === 'munin_theme');
  expect(cookie?.value).toBe('light');
  expect(cookie?.domain.replace(/^\./, ''), 'shared across the subdomains').toBe(siteDomain(page));

  for (const host of ['', 'docs.', 'search.']) {
    await page.goto(`https://${host}${cookie!.domain.replace(/^\./, '')}/`);
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  }

  // The header switch on a static page, back to dark.
  await page.getByRole('combobox', { name: 'Theme' }).first().selectOption('dark');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.goto('/');
  await expectTheme(page, 'dark');
});

test('signed-in screens in light: chat with code, settings, knowledge, admin, phone', async ({ page, context }) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('response', (r) => { if (r.status() >= 500) errors.push(`${r.status()} ${new URL(r.url()).pathname}`); });

  await page.goto('/');
  await context.addCookies([{ name: 'munin_theme', value: 'light', domain: `.${siteDomain(page)}`, path: '/' }]);
  await page.reload();
  await expectTheme(page, 'light');
  await shot(page, 'live-home-light');

  let id: string | null = null;
  try {
    await page.getByTestId('composer-textarea').fill(
      `${RUN}: reply with only a Python code block of three lines: a comment, a string assignment and a print call.`,
    );
    await page.getByTestId('composer-send').click();
    // Keep the address as soon as there is one, so a failure below still deletes the chat.
    await expect(page).toHaveURL(/\/c\/.+/, { timeout: 60_000 });
    id = new URL(page.url()).pathname.match(/^\/c\/(.+)$/)?.[1] ?? null;
    // The highlighter renders its block as a div, not a pre.
    const code = page.locator('code.language-python').first();
    await expect(code).toBeVisible({ timeout: 120_000 });
    await expect(page.getByTestId('composer-stop')).toBeHidden({ timeout: 60_000 });
    await shot(page, 'live-chat-light');
    await reportContrast(page, 'chat (light)');

    // The code block follows a switch without a reload.
    const lightBg = await code.locator('xpath=..').evaluate((el) => getComputedStyle(el).backgroundColor);
    await openSettings(page);
    await page.getByRole('radio', { name: 'Dark' }).check({ force: true });
    await page.getByRole('button', { name: 'Back to chat' }).click();
    await expectTheme(page, 'dark');
    await expect(code).toBeVisible();
    const darkBg = await code.locator('xpath=..').evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(darkBg, 'code block recoloured').not.toBe(lightBg);
    await shot(page, 'live-chat-dark');
    await reportContrast(page, 'chat (dark)');

    await openSettings(page);
    await page.getByRole('radio', { name: 'Light' }).check({ force: true });
    await expectTheme(page, 'light');
    await shot(page, 'live-settings-light');
    await page.getByRole('button', { name: 'Back to chat' }).click();
  } finally {
    if (id) await page.request.delete(`/api/chats/${id}`);
  }

  await page.goto('/knowledge');
  await page.waitForLoadState('networkidle');
  await expectTheme(page, 'light');
  await shot(page, 'live-knowledge-light');
  await reportContrast(page, 'knowledge (light)');

  await page.goto('/');
  const me = await page.request.get(`https://auth.${siteDomain(page)}/auth/me`);
  await page.getByText((await me.json()).email).first().click();
  const admin = page.getByRole('menuitem', { name: /Admin/ });
  if (await admin.isVisible()) {
    await admin.click();
    await page.waitForLoadState('networkidle');
    await shot(page, 'live-admin-light');
    await reportContrast(page, 'admin (light)');
  } else {
    await page.keyboard.press('Escape');
    test.info().annotations.push({ type: 'Admin', description: 'not offered: not an admin account' });
  }

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expectTheme(page, 'light');
  await shot(page, 'live-phone-light');

  expect(errors).toEqual([]);
});
